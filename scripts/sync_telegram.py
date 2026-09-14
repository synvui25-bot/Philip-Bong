import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

from scripts.telegram_client import fetch_bot_channel_status, fetch_updates
from scripts.telegram_media import MediaError, download_bot_media, download_public_media, save_webp
from scripts.telegram_parser import parse_listing
from scripts.telegram_public import PublicHistoryError, reconcile_missing, scan_public_history


CHANNEL_USERNAME = "sarawakpropertyguru"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LISTINGS_PATH = ROOT / "data" / "telegram-listings.json"
DEFAULT_STATE_PATH = ROOT / "data" / "telegram-sync-state.json"
DEFAULT_MEDIA_PATH = ROOT / "assets" / "telegram"


def _photo_file_ids(message: dict) -> list[str]:
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        file_id = photos[-1].get("file_id") if isinstance(photos[-1], dict) else None
        if isinstance(file_id, str):
            return [file_id]
    document = message.get("document")
    if isinstance(document, dict):
        mime_type = document.get("mime_type")
        file_id = document.get("file_id")
        if isinstance(mime_type, str) and mime_type.startswith("image/") and isinstance(file_id, str):
            return [file_id]
    return []


def group_media_posts(messages: list[dict]) -> list[dict]:
    """Collapse Telegram media groups while retaining a caption-bearing primary post."""
    groups: dict[str, list[dict]] = {}
    grouped: list[dict] = []

    # A Bot API message is a complete snapshot, including an empty caption.
    # Coalesce edits before grouping so stale captions/photos cannot accumulate.
    latest = {}
    for message in messages:
        if not isinstance(message.get("message_id"), int):
            raise ValueError("Telegram post is missing an integer message_id")
        latest[message["message_id"]] = message
    for message in latest.values():
        media_group_id = message.get("media_group_id")
        if isinstance(media_group_id, str) and media_group_id:
            groups.setdefault(media_group_id, []).append(message)
        else:
            item = dict(message)
            item["file_ids"] = _photo_file_ids(message)
            grouped.append(item)

    for group in groups.values():
        group.sort(key=lambda message: message["message_id"])
        primary = next(
            (
                message
                for message in reversed(group)
                if isinstance(message.get("caption") or message.get("text"), str)
                and (message.get("caption") or message.get("text")).strip()
            ),
            group[0],
        )
        item = dict(primary)
        file_ids = [
            file_id
            for message in group
            for file_id in _photo_file_ids(message)
        ]
        item["file_ids"] = list(dict.fromkeys(file_ids))
        members = {}
        for message in group:
            key = str(message["message_id"])
            members[key] = list(dict.fromkeys([*members.get(key, []), *_photo_file_ids(message)]))
        item["album_members"] = members
        item["_edited_message_ids"] = [member["message_id"] for member in group
                                       if member.get("_sync_event_type") == "edited_channel_post"]
        edit_dates = [member["edit_date"] for member in group if _is_valid_published_at(member.get("edit_date"))]
        if edit_dates:
            item["edit_date"] = max(edit_dates)
        grouped.append(item)

    return grouped


def _message_id(listing: dict) -> int:
    message_id = listing.get("message_id")
    if isinstance(message_id, int):
        return message_id
    identifier = listing.get("id", "")
    if isinstance(identifier, str) and identifier.startswith("telegram-"):
        return int(identifier.removeprefix("telegram-"))
    return -1


def _published_sort_value(listing: dict) -> float:
    value = listing.get("published_at", 0)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _is_valid_published_at(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _validate_channel(message: dict) -> None:
    chat = message.get("chat")
    username = chat.get("username") if isinstance(chat, dict) else None
    if username != CHANNEL_USERNAME:
        raise ValueError(f"only @{CHANNEL_USERNAME} updates may be synchronized")


def apply_updates(listings: list[dict], updates: list[dict]) -> tuple[list[dict], int]:
    """Apply one Bot API batch and return normalized listings plus the next offset."""
    by_id = {listing.get("id"): dict(listing) for listing in listings if listing.get("id")}
    seen_update_ids: set[int] = set()
    messages: list[dict] = []
    processed_update_ids: list[int] = []

    for update in sorted(updates, key=lambda item: item.get("update_id", -1)):
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            raise ValueError("Telegram update is missing an integer update_id")
        if update_id in seen_update_ids:
            continue
        seen_update_ids.add(update_id)
        processed_update_ids.append(update_id)

        event_type = next(
            (
                name
                for name in ("channel_post", "edited_channel_post")
                if isinstance(update.get(name), dict)
            ),
            None,
        )
        if event_type is None:
            continue

        message = dict(update[event_type])
        _validate_channel(message)
        message["_sync_event_type"] = event_type
        message["_sync_update_id"] = update_id
        messages.append(message)

    for message in group_media_posts(messages):
        message_id = message.get("message_id")
        if not isinstance(message_id, int):
            raise ValueError("Telegram post is missing an integer message_id")

        identifier = f"telegram-{message_id}"
        media_group_id = message.get("media_group_id")
        existing_album = next(
            (item for item in by_id.values() if media_group_id
             and (item.get("media_group_id") == media_group_id
                  or (not item.get("media_group_id") and message_id in item.get("message_ids", [])))), None
        )
        legacy = by_id.get(identifier)
        if existing_album is None and "public_album_members" in message and legacy:
            existing_album = legacy
        if (existing_album is None and media_group_id and legacy
                and not legacy.get("media_group_id") and "album_members" not in legacy):
            # Base-version records kept only the primary ID and flat file IDs.
            # The matching primary post can establish their group identity, but
            # cannot identify or authorize removal of the other stored photos.
            existing_album = legacy
        if existing_album:
            identifier = existing_album["id"]
        listing = parse_listing(message)
        has_listing_caption = listing is not None
        caption_owner = (existing_album or {}).get("caption_message_id", (existing_album or {}).get("message_id"))
        if listing is None:
            if existing_album and caption_owner not in message.get("_edited_message_ids", [message_id]):
                # Telegram sends each album member independently, including later
                # photo edits with no caption. Such an event does not clear the
                # caption-bearing listing or replace its other members.
                listing = dict(existing_album)
            elif message.get("_sync_event_type") == "edited_channel_post" or message.get("_edited_message_ids"):
                by_id.pop(identifier, None)
                continue
            else:
                print(
                    "Telegram update skipped: "
                    f"update_id={message.get('_sync_update_id')} "
                    f"message_id={message_id} "
                    f"event={message.get('_sync_event_type')} "
                    f"has_text={bool(message.get('text') or message.get('caption'))} "
                    f"has_photo={bool(message.get('photo'))} "
                    f"has_image_document={bool(message.get('file_ids'))} "
                    "reason=not_a_listing",
                    file=sys.stderr,
                )
                continue

        published_at = message.get("date")
        if not _is_valid_published_at(published_at):
            continue

        listing["message_id"] = existing_album["message_id"] if existing_album else message_id
        listing["id"] = identifier
        listing["telegram_url"] = f"https://t.me/{CHANNEL_USERNAME}/{listing['message_id']}"
        listing["published_at"] = existing_album["published_at"] if existing_album else published_at
        if has_listing_caption:
            # A public wrapper cannot override caption ownership learned from
            # an individual Bot message in an earlier run.
            listing["caption_message_id"] = (existing_album.get("caption_message_id", message_id)
                                              if existing_album and "public_album_members" in message
                                              else message.get("caption_message_id", message_id))
        edit_dates = [value for value in [message.get("edit_date"), (existing_album or {}).get("edited_at")]
                      if _is_valid_published_at(value)]
        if edit_dates:
            listing["edited_at"] = max(edit_dates)
        for key in ("message_ids", "public_album_members", "media_urls"):
            if key in message and (key != "media_urls" or not media_group_id):
                listing[key] = message[key]
            elif existing_album and key in existing_album:
                listing[key] = existing_album[key]
        if existing_album and "message_ids" in listing:
            listing["message_ids"] = sorted(set(existing_album.get("message_ids", [])) | set(listing["message_ids"]))
        if existing_album and "public_album_members" in listing:
            listing["public_album_members"] = {**existing_album.get("public_album_members", {}), **listing["public_album_members"]}
        if media_group_id:
            # A media_group_id identifies the complete album. An incoming batch
            # describes only the members it contains, not an authoritative album
            # replacement. Update those members and retain the persisted others.
            members = {**(existing_album or {}).get("album_members", {}), **message["album_members"]}
            listing["media_group_id"] = media_group_id
            listing["album_members"] = {key: members[key] for key in sorted(members, key=int)}
            listing["message_ids"] = sorted(set(listing.get("message_ids", [])) | {int(key) for key in members})
            listing["file_ids"] = list(dict.fromkeys(
                file_id for photos in listing["album_members"].values() for file_id in photos
            ))
            old = existing_album or {}
            if existing_album and ("album_members" not in old or "album_unmapped_file_ids" in old):
                known = set(listing["file_ids"])
                unmapped = [file_id for file_id in old.get("album_unmapped_file_ids", old.get("file_ids", []))
                            if file_id not in known]
                old_owners = {file_id: member_id for member_id, photos in old.get("album_members", {}).items()
                              for file_id in photos}
                ordered = []
                for file_id in old.get("file_ids", []):
                    if file_id in old_owners:
                        ordered.extend(members[old_owners[file_id]])
                    elif file_id in known or file_id in unmapped:
                        ordered.append(file_id)
                # Claim unknown photos only when their actual file ID arrives in
                # a member event. Preserve prior slots for established members.
                listing["album_unmapped_file_ids"] = unmapped
                listing["file_ids"] = list(dict.fromkeys([*ordered, *listing["file_ids"], *unmapped]))
        else:
            listing["file_ids"] = message.get("file_ids", [])
        by_id[identifier] = listing

    normalized = sorted(
        by_id.values(),
        key=lambda listing: (_published_sort_value(listing), _message_id(listing)),
        reverse=True,
    )
    offset = max(processed_update_ids) + 1 if processed_update_ids else 0
    return normalized, offset


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _stage_bytes(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _commit_files(payloads: dict[Path, bytes]) -> None:
    """Stage every file first; roll back completed replacements on an I/O error.

    Each rename is atomic. Separate files cannot form an OS-level transaction;
    the workflow must serialize sync processes and publish only successful runs.
    """
    staged = {}
    backups = {}
    replaced = []
    try:
        for path, content in payloads.items():
            if path.exists() and path.read_bytes() == content:
                continue
            staged[path] = _stage_bytes(path, content)
            backups[path] = _stage_bytes(path, path.read_bytes()) if path.exists() else None
        for path, temporary in staged.items():
            os.replace(temporary, path)
            replaced.append(path)
    except BaseException:
        for path in reversed(replaced):
            if backups[path] is None:
                path.unlink(missing_ok=True)
            else:
                os.replace(backups[path], path)
        raise
    finally:
        for temporary in [*staged.values(), *backups.values()]:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _json_bytes(payload) -> bytes:
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def _asset_path(image: str, media_path: Path) -> Path | None:
    # Never let stored JSON paths authorize deletion outside our exact namespace.
    if isinstance(image, str) and re.fullmatch(r"assets/telegram/[1-9][0-9]*-[0-9]+\.webp", image):
        path = media_path / image.rsplit("/", 1)[1]
        if path.resolve().parent == media_path.resolve() and not path.is_symlink():
            return path
    return None


def _prepare_media(listings, previous, token, media_path):
    old_by_id = {item["id"]: item for item in previous}
    payloads = {}
    for listing in listings:
        old = old_by_id.get(listing["id"], {})
        urls = listing.get("media_urls", [])
        file_ids = listing.get("file_ids", [])
        old_images = [image for image in old.get("images", [])
                      if (path := _asset_path(image, media_path)) is not None and path.is_file()]
        if not urls and not file_ids:
            # An authoritative removal of the final known source must also drop
            # its image; legacy records without source metadata keep their fallback.
            listing["images"] = [] if old.get("media_urls") or old.get("file_ids") else old_images
            continue
        if (old_images and urls == old.get("media_urls", [])
                and file_ids == old.get("file_ids", []) and not old.get("media_retry")):
            listing["images"] = old_images
            continue
        images = []
        generated = {}
        hashes = set()
        try:
            # Conversion stays in a private staging directory until all public parsing
            # and listing processing succeeded. A failed album retains the old album.
            media_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=media_path.parent, prefix=".telegram-") as folder:
                for source in urls or file_ids:
                    content = download_public_media(source) if urls else download_bot_media(token, source)
                    digest = hashlib.sha256(content).hexdigest()
                    if digest in hashes:
                        continue
                    hashes.add(digest)
                    name = f"{listing['message_id']}-{len(images)}.webp"
                    staged_image = Path(folder) / name
                    save_webp(content, staged_image)
                    generated[media_path / name] = staged_image.read_bytes()
                    images.append(f"assets/telegram/{name}")
            listing["images"] = images
            listing.pop("media_retry", None)
            payloads.update(generated)
        except MediaError as exc:
            print(
                f"Telegram media unavailable: listing={listing['id']} reason={exc}",
                file=sys.stderr,
            )
            listing["images"] = old_images
            listing["media_retry"] = True
    return payloads


def _persist_sync(listings, previous, state, token, listings_path, state_path, media_path):
    payloads = _prepare_media(listings, previous, token, media_path)
    payloads[listings_path] = _json_bytes(listings)
    payloads[state_path] = _json_bytes(state)
    _commit_files(payloads)
    retained = {image for listing in listings for image in listing.get("images", [])}
    obsolete = {image for listing in previous for image in listing.get("images", [])} - retained
    for image in sorted(obsolete):
        path = _asset_path(image, media_path)
        if path is not None:
            path.unlink(missing_ok=True)


def _visible_ids(posts):
    return {message_id for post in posts
            for message_id in post.get("message_ids", [post["message_id"]])}


def _refresh_public_albums(listings, posts):
    """Refresh known public media; missing members await two-scan reconciliation."""
    visible = {message_id: post for post in posts
               for message_id in post.get("message_ids", [post["message_id"]])}
    refreshed = []
    for stored in listings:
        listing = dict(stored)
        post = visible.get(listing["message_id"])
        if "media_urls" in listing and post is not None:
            listing["message_ids"] = sorted(set(listing.get("message_ids", [listing["message_id"]]))
                                             | set(post.get("message_ids", [post["message_id"]])))
            if "public_album_members" in post:
                members = {**listing.get("public_album_members", {}), **post["public_album_members"]}
                listing["public_album_members"] = {key: members[key] for key in sorted(members, key=int)}
                listing["media_urls"] = list(dict.fromkeys(url for urls in listing["public_album_members"].values() for url in urls))
            else:
                listing["media_urls"] = post["media_urls"]
        refreshed.append(listing)
    return refreshed


def run_bootstrap(
    listings_path: Path = DEFAULT_LISTINGS_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
    media_path: Path = DEFAULT_MEDIA_PATH,
) -> list[dict]:
    previous = _read_json(listings_path, [])
    state = _read_json(state_path, {"last_update_id": 0})
    posts = scan_public_history()
    updates = [{"update_id": index, "channel_post": post} for index, post in enumerate(posts)]
    listings, _ = apply_updates(previous, updates)
    by_id = {post["message_id"]: post for post in posts}
    for listing in listings:
        if listing["message_id"] in by_id:
            listing["media_urls"] = by_id[listing["message_id"]]["media_urls"]
    listings = _refresh_public_albums(listings, posts)
    listings, state = reconcile_missing(listings, _visible_ids(posts), state)
    _persist_sync(listings, previous, state, "", listings_path, state_path, media_path)
    return listings


def run_incremental(
    token: str,
    listings_path: Path = DEFAULT_LISTINGS_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
    media_path: Path = DEFAULT_MEDIA_PATH,
) -> tuple[list[dict], int]:
    """Fetch and persist updates after the saved Bot API offset."""
    listings = _read_json(listings_path, [])
    state = _read_json(state_path, {"last_update_id": 0})
    saved_offset = state.get("last_update_id", 0)
    if not isinstance(saved_offset, int):
        raise ValueError("last_update_id must be an integer")

    updates = fetch_updates(token, saved_offset)
    public_scan_complete = True
    try:
        visible_posts = scan_public_history()
    except PublicHistoryError as exc:
        public_scan_complete = False
        visible_posts = []
        print(f"Warning: public preview unavailable; processing bot updates only ({exc}).", file=sys.stderr)
    hydrated = _refresh_public_albums(listings, visible_posts)
    updated_listings, next_offset = apply_updates(hydrated, updates)
    next_offset = max(saved_offset, next_offset)
    if public_scan_complete:
        updated_listings, state = reconcile_missing(
            updated_listings, _visible_ids(visible_posts), state
        )
    _persist_sync(updated_listings, listings, {**state, "last_update_id": next_offset},
                  token, listings_path, state_path, media_path)
    return updated_listings, next_offset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synchronize Telegram channel listings")
    parser.add_argument("--bootstrap", action="store_true", help="import public channel history")
    args = parser.parse_args(argv)
    try:
        if args.bootstrap:
            run_bootstrap()
        else:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            status = fetch_bot_channel_status(token, CHANNEL_USERNAME)
            print(
                "Telegram bot check: "
                f"@{status['bot_username']} is {status['channel_status']} "
                f"in @{CHANNEL_USERNAME}.",
                file=sys.stderr,
            )
            if status["channel_status"] not in {"administrator", "creator"}:
                raise RuntimeError(
                    f"@{status['bot_username']} is not an administrator of @{CHANNEL_USERNAME}"
                )
            run_incremental(token)
    except Exception as exc:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        details = str(exc)
        if token:
            details = details.replace(token, "[REDACTED]")
        print(
            f"Telegram synchronization failed ({type(exc).__name__}: {details}); "
            "no successful publication was produced.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

