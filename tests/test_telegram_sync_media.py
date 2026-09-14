import json
from io import BytesIO

from PIL import Image
import pytest

from scripts import sync_telegram as sync
from scripts.telegram_public import PublicHistoryError
from scripts.telegram_media import MediaError


def post(message_id=20):
    return {"message_id": message_id, "date": 100, "chat": {"username": "sarawakpropertyguru"},
            "text": "House For Sale RM500,000", "photo": True,
            "media_urls": ["https://cdn1.cdn-telegram.org/a.jpg", "https://cdn1.cdn-telegram.org/b.jpg"]}


def picture(color="red"):
    content = BytesIO()
    Image.new("RGB", (10, 10), color).save(content, "PNG")
    return content.getvalue()


def setup_files(tmp_path, listings=None, state=None):
    listings_path = tmp_path / "data/listings.json"
    state_path = tmp_path / "data/state.json"
    listings_path.parent.mkdir()
    listings_path.write_text(json.dumps(listings or []), encoding="utf-8")
    state_path.write_text(json.dumps(state or {"last_update_id": 5}), encoding="utf-8")
    return listings_path, state_path, tmp_path / "assets/telegram"


def test_bootstrap_imports_album_with_deterministic_images_without_bot_token(monkeypatch, tmp_path):
    paths = setup_files(tmp_path)
    monkeypatch.setattr(sync, "scan_public_history", lambda: [post(20), post(30)])
    monkeypatch.setattr(sync, "download_public_media", lambda url: picture("red" if "a.jpg" in url else "blue"))
    sync.run_bootstrap(*paths)
    data = json.loads(paths[0].read_text())
    assert [item["message_id"] for item in data] == [30, 20]
    assert data[1]["images"] == ["assets/telegram/20-0.webp", "assets/telegram/20-1.webp"]
    assert sorted(file.name for file in paths[2].iterdir()) == ["20-0.webp", "20-1.webp", "30-0.webp", "30-1.webp"]
    assert json.loads(paths[1].read_text())["last_update_id"] == 5


def test_normal_sync_reconciles_but_does_not_import_public_history(monkeypatch, tmp_path):
    paths = setup_files(tmp_path)
    monkeypatch.setattr(sync, "scan_public_history", lambda: [post()])
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: [])
    sync.run_incremental("secret", *paths)
    assert json.loads(paths[0].read_text()) == []


def test_normal_sync_downloads_bot_photos_and_deduplicates_identical_bytes(monkeypatch, tmp_path):
    paths = setup_files(tmp_path)
    monkeypatch.setattr(sync, "scan_public_history", lambda: [post()])
    messages = [{**post(), "caption": "House For Sale RM500,000", "media_group_id": "a", "photo": [{"file_id": "p1"}]},
                {"message_id": 21, "date": 100, "chat": {"username": "sarawakpropertyguru"}, "media_group_id": "a", "photo": [{"file_id": "p2"}]}]
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: [{"update_id": i + 5, "channel_post": item} for i, item in enumerate(messages)])
    monkeypatch.setattr(sync, "download_bot_media", lambda token, file_id: picture())
    sync.run_incremental("secret", *paths)
    data = json.loads(paths[0].read_text())
    assert data[0]["images"] == ["assets/telegram/20-0.webp"]
    assert json.loads(paths[1].read_text())["last_update_id"] == 7


def test_failed_bootstrap_scan_preserves_json_counts_and_existing_media(monkeypatch, tmp_path):
    paths = setup_files(tmp_path, [{"id": "telegram-20", "message_id": 20, "images": ["assets/telegram/20-0.webp"]}],
                        {"last_update_id": 5, "missing_counts": {"20": 1}})
    paths[2].mkdir(parents=True)
    image_path = paths[2] / "20-0.webp"
    image_path.write_bytes(b"old image")
    before = [path.read_bytes() for path in paths[:2]]
    def fail():
        raise PublicHistoryError("incomplete scan")
    monkeypatch.setattr(sync, "scan_public_history", fail)
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: [])
    with pytest.raises(PublicHistoryError):
        sync.run_bootstrap(*paths)
    assert [path.read_bytes() for path in paths[:2]] == before
    assert image_path.read_bytes() == b"old image"


def test_incremental_sync_processes_bot_updates_when_public_preview_fails(monkeypatch, tmp_path, capsys):
    paths = setup_files(tmp_path, [{"id": "telegram-20", "message_id": 20}],
                        {"last_update_id": 5, "missing_counts": {"20": 1}})
    def fail():
        raise PublicHistoryError("temporary preview failure")
    monkeypatch.setattr(sync, "scan_public_history", fail)
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: [{
        "update_id": 5,
        "channel_post": {"message_id": 30, "text": "House For Sale RM500,000",
                         "chat": {"username": "sarawakpropertyguru"},
                         "date": 1789300000,
                         "photo": [{"file_id": "new-photo"}]},
    }])

    listings, offset = sync.run_incremental("secret", *paths)

    assert {item["message_id"] for item in listings} == {20, 30}
    assert offset == 6
    assert json.loads(paths[1].read_text())["missing_counts"] == {"20": 1}
    assert "public preview unavailable" in capsys.readouterr().err.lower()


def test_second_complete_scan_deletes_only_removed_listing_assets(monkeypatch, tmp_path):
    paths = setup_files(tmp_path, [{"id": "telegram-20", "message_id": 20, "images": ["assets/telegram/20-0.webp"]}])
    paths[2].mkdir(parents=True)
    image_path = paths[2] / "20-0.webp"
    image_path.write_bytes(b"old image")
    unrelated = paths[2] / "keep.webp"
    unrelated.write_bytes(b"keep")
    monkeypatch.setattr(sync, "scan_public_history", lambda: [post(30)])
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: [])
    sync.run_incremental("secret", *paths)
    assert image_path.exists()
    assert json.loads(paths[1].read_text())["missing_counts"] == {"20": 1}
    sync.run_incremental("secret", *paths)
    assert not image_path.exists()
    assert unrelated.read_bytes() == b"keep"
    assert json.loads(paths[0].read_text()) == []


def test_media_failure_retains_existing_image(monkeypatch, tmp_path):
    existing = {"id": "telegram-20", "message_id": 20, "images": ["assets/telegram/20-0.webp"]}
    paths = setup_files(tmp_path, [existing])
    paths[2].mkdir(parents=True)
    (paths[2] / "20-0.webp").write_bytes(b"old image")
    monkeypatch.setattr(sync, "scan_public_history", lambda: [post()])
    def fail(url):
        raise MediaError("failed image")
    monkeypatch.setattr(sync, "download_public_media", fail)
    sync.run_bootstrap(*paths)
    assert json.loads(paths[0].read_text())[0]["images"] == existing["images"]
    assert (paths[2] / "20-0.webp").read_bytes() == b"old image"


def test_json_stage_failure_does_not_change_state_or_delete_media(monkeypatch, tmp_path):
    paths = setup_files(tmp_path, [{"id": "telegram-20", "message_id": 20}], {"missing_counts": {"20": 1}})
    before = [path.read_bytes() for path in paths[:2]]
    monkeypatch.setattr(sync, "scan_public_history", lambda: [post(30)])
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: [])
    original = sync.os.replace
    def fail_state(source, destination):
        if destination == paths[1]:
            raise OSError("disk error")
        return original(source, destination)
    monkeypatch.setattr(sync.os, "replace", fail_state)
    with pytest.raises(OSError):
        sync.run_incremental("secret", *paths)
    assert [path.read_bytes() for path in paths[:2]] == before


def test_cli_requires_explicit_bootstrap_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(sync, "run_bootstrap", lambda: calls.append("bootstrap"))
    monkeypatch.setattr(sync, "run_incremental", lambda token: calls.append("incremental"))
    monkeypatch.setattr(
        sync,
        "fetch_bot_channel_status",
        lambda token, channel: {
            "bot_id": 42,
            "bot_username": "PhilipBListingSyncBot",
            "channel_status": "administrator",
        },
    )
    assert sync.main([]) == 0
    assert sync.main(["--bootstrap"]) == 0
    assert calls == ["incremental", "bootstrap"]


def test_visible_album_member_is_not_counted_missing(monkeypatch, tmp_path):
    paths = setup_files(tmp_path, [{"id": "telegram-21", "message_id": 21}], {"missing_counts": {"21": 1}})
    monkeypatch.setattr(sync, "scan_public_history", lambda: [{**post(), "message_ids": [20, 21]}])
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: [])
    sync.run_incremental("secret", *paths)
    assert json.loads(paths[0].read_text())[0]["message_id"] == 21
    assert json.loads(paths[1].read_text())["missing_counts"] == {}


def test_existing_media_is_not_replaced_when_later_image_fails(monkeypatch, tmp_path):
    paths = setup_files(tmp_path, [{"id": "telegram-20", "message_id": 20, "images": ["assets/telegram/20-0.webp"]}])
    paths[2].mkdir(parents=True)
    image_path = paths[2] / "20-0.webp"
    image_path.write_bytes(b"old image")
    monkeypatch.setattr(sync, "scan_public_history", lambda: [post()])
    def download(url):
        if "b.jpg" in url:
            raise MediaError("timeout")
        return picture()
    monkeypatch.setattr(sync, "download_public_media", download)
    sync.run_bootstrap(*paths)
    assert image_path.read_bytes() == b"old image"
    assert list(paths[2].iterdir()) == [image_path]


def test_second_scan_does_not_delete_path_outside_asset_directory(monkeypatch, tmp_path):
    outside = tmp_path / "keep.webp"
    outside.write_bytes(b"keep")
    paths = setup_files(tmp_path, [{"id": "telegram-20", "message_id": 20, "images": [str(outside), "assets/telegram/../../keep.webp"]}],
                        {"missing_counts": {"20": 1}})
    monkeypatch.setattr(sync, "scan_public_history", lambda: [post(30)])
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: [])
    sync.run_incremental("secret", *paths)
    assert outside.read_bytes() == b"keep"


def test_cli_failed_scan_returns_nonzero_and_does_not_print_token(monkeypatch, capsys):
    def fail(token):
        raise PublicHistoryError(f"failed https://api.telegram.org/bot{token}/getUpdates")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setattr(
        sync,
        "fetch_bot_channel_status",
        lambda token, channel: {
            "bot_id": 42,
            "bot_username": "PhilipBListingSyncBot",
            "channel_status": "administrator",
        },
    )
    monkeypatch.setattr(sync, "run_incremental", fail)
    assert sync.main([]) == 1
    error = capsys.readouterr().err
    assert "secret-token" not in error
    assert "PublicHistoryError" in error


def test_unchanged_bootstrap_preserves_json_and_asset_mtimes(monkeypatch, tmp_path):
    paths = setup_files(tmp_path)
    monkeypatch.setattr(sync, "scan_public_history", lambda: [post()])
    monkeypatch.setattr(sync, "download_public_media", lambda url: picture())
    sync.run_bootstrap(*paths)
    files = [*paths[:2], paths[2] / "20-0.webp"]
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in files]
    def unexpected_download(url):
        raise AssertionError("unchanged album should reuse its processed images")
    monkeypatch.setattr(sync, "download_public_media", unexpected_download)
    sync.run_bootstrap(*paths)
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in files] == before


def test_persistence_failure_rolls_back_replaced_image_and_listing_json(monkeypatch, tmp_path):
    paths = setup_files(tmp_path, [{"id": "telegram-20", "message_id": 20, "images": ["assets/telegram/20-0.webp"]}])
    paths[2].mkdir(parents=True)
    image_path = paths[2] / "20-0.webp"
    image_path.write_bytes(b"old image")
    before = [path.read_bytes() for path in paths[:2]]
    monkeypatch.setattr(sync, "scan_public_history", lambda: [post()])
    monkeypatch.setattr(sync, "download_public_media", lambda url: picture())
    original_replace = sync.os.replace
    def fail_state(source, destination):
        if destination == paths[1]:
            raise OSError("state write failed")
        return original_replace(source, destination)
    monkeypatch.setattr(sync.os, "replace", fail_state)
    with pytest.raises(OSError):
        sync.run_bootstrap(*paths)
    assert [path.read_bytes() for path in paths[:2]] == before
    assert image_path.read_bytes() == b"old image"
    assert list(paths[2].iterdir()) == [image_path]


def test_caption_edit_in_later_sync_preserves_complete_album(monkeypatch, tmp_path):
    paths = setup_files(tmp_path)
    caption = {"message_id": 20, "date": 100, "chat": {"username": "sarawakpropertyguru"},
               "caption": "House For Sale RM500,000", "media_group_id": "album-a", "photo": [{"file_id": "p1"}]}
    second = {"message_id": 21, "date": 100, "chat": {"username": "sarawakpropertyguru"},
              "media_group_id": "album-a", "photo": [{"file_id": "p2"}]}
    batches = {
        5: [{"update_id": 5, "channel_post": caption}, {"update_id": 6, "channel_post": second}],
        7: [{"update_id": 7, "edited_channel_post": {**caption, "caption": "House For Sale RM480,000"}}],
    }
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: batches[offset])
    monkeypatch.setattr(sync, "scan_public_history", lambda: [{**post(), "message_ids": [20, 21]}])
    monkeypatch.setattr(sync, "download_bot_media", lambda token, file_id: picture("red" if file_id == "p1" else "blue"))
    sync.run_incremental("secret", *paths)
    second_asset = paths[2] / "20-1.webp"
    before = second_asset.read_bytes()
    sync.run_incremental("secret", *paths)
    listing = json.loads(paths[0].read_text())[0]
    assert listing["price"] == "RM480,000"
    assert listing["file_ids"] == ["p1", "p2"]
    assert listing["images"] == ["assets/telegram/20-0.webp", "assets/telegram/20-1.webp"]
    assert second_asset.read_bytes() == before


def test_bot_caption_edit_after_public_bootstrap_preserves_public_album(monkeypatch, tmp_path):
    paths = setup_files(tmp_path)
    monkeypatch.setattr(sync, "scan_public_history", lambda: [post()])
    monkeypatch.setattr(sync, "download_public_media", lambda url: picture("red" if "a.jpg" in url else "blue"))
    monkeypatch.setattr(sync, "download_bot_media", lambda token, file_id: picture())
    sync.run_bootstrap(*paths)
    second_asset = paths[2] / "20-1.webp"
    before = second_asset.read_bytes()
    edit = {"message_id": 20, "date": 100, "chat": {"username": "sarawakpropertyguru"},
            "caption": "House For Sale RM480,000", "media_group_id": "album-a", "photo": [{"file_id": "p1"}]}
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: [{"update_id": 5, "edited_channel_post": edit}])
    sync.run_incremental("secret", *paths)
    listing = json.loads(paths[0].read_text())[0]
    assert listing["price"] == "RM480,000"
    assert listing["images"] == ["assets/telegram/20-0.webp", "assets/telegram/20-1.webp"]
    assert second_asset.read_bytes() == before


@pytest.mark.parametrize("stored_group", [False, True], ids=["base-record", "group-without-members"])
def test_legacy_album_survives_first_upgrade_edit_and_later_known_member_replacement(monkeypatch, tmp_path, stored_group):
    legacy = {"id": "telegram-20", "message_id": 20, "published_at": 100,
              "title": "House For Sale RM500,000", "price": "RM500,000",
              "telegram_url": "https://t.me/sarawakpropertyguru/20",
              "file_ids": ["p1", "p2"],
              "images": ["assets/telegram/20-0.webp", "assets/telegram/20-1.webp"]}
    if stored_group:
        legacy["media_group_id"] = "album-a"
    paths = setup_files(tmp_path, [legacy])
    sync.save_webp(picture("red"), paths[2] / "20-0.webp")
    sync.save_webp(picture("blue"), paths[2] / "20-1.webp")
    second_asset = paths[2] / "20-1.webp"
    before = second_asset.read_bytes()
    caption = {"message_id": 20, "date": 100, "chat": {"username": "sarawakpropertyguru"},
               "caption": "House For Sale RM480,000", "media_group_id": "album-a", "photo": [{"file_id": "p1"}]}
    batches = {
        5: [{"update_id": 5, "edited_channel_post": caption}],
        6: [{"update_id": 6, "edited_channel_post": {**caption, "photo": [{"file_id": "p3"}]}}],
    }
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: batches[offset])
    monkeypatch.setattr(sync, "scan_public_history", lambda: [{**post(), "message_ids": [20, 21]}])
    monkeypatch.setattr(sync, "download_bot_media", lambda token, file_id: picture({"p1": "red", "p2": "blue", "p3": "green"}[file_id]))
    sync.run_incremental("secret", *paths)
    migrated = json.loads(paths[0].read_text())[0]
    assert migrated["price"] == "RM480,000"
    assert migrated["file_ids"] == ["p1", "p2"]
    assert migrated["images"] == legacy["images"]
    assert second_asset.read_bytes() == before
    sync.run_incremental("secret", *paths)
    replaced = json.loads(paths[0].read_text())[0]
    assert replaced["file_ids"] == ["p3", "p2"]
    assert replaced["images"] == legacy["images"]
    assert second_asset.read_bytes() == before


def public_album():
    return {**post(), "message_ids": [20, 21], "caption_message_id": 20,
            "public_album_members": {"20": ["https://cdn1.cdn-telegram.org/a.jpg"],
                                     "21": ["https://cdn1.cdn-telegram.org/b.jpg"]}}


@pytest.mark.parametrize("repeat_bootstrap", [False, True])
def test_public_album_member_edit_updates_stable_listing_then_caption_clear_removes_it(monkeypatch, tmp_path, repeat_bootstrap):
    paths = setup_files(tmp_path)
    monkeypatch.setattr(sync, "scan_public_history", lambda: [public_album()])
    monkeypatch.setattr(sync, "download_public_media", lambda url: picture("red" if "a.jpg" in url else "blue"))
    monkeypatch.setattr(sync, "download_bot_media", lambda token, file_id: picture("blue"))
    sync.run_bootstrap(*paths)
    stored = json.loads(paths[0].read_text())[0]
    assert stored.get("message_ids") == [20, 21]
    assert stored.get("caption_message_id") == 20
    edit = {"message_id": 21, "date": 100, "edit_date": 200, "chat": {"username": "sarawakpropertyguru"},
            "caption": "House For Sale RM480,000", "media_group_id": "album-a", "photo": [{"file_id": "p2"}]}
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: [{"update_id": 5, "edited_channel_post": edit}])
    sync.run_incremental("secret", *paths)
    updated = json.loads(paths[0].read_text())
    assert len(updated) == 1
    assert updated[0]["id"] == "telegram-20"
    assert updated[0]["price"] == "RM480,000"
    assert updated[0]["caption_message_id"] == 21
    assert len(updated[0]["images"]) == 2
    if repeat_bootstrap:
        sync.run_bootstrap(*paths)
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: [{"update_id": 6, "edited_channel_post": {**edit, "caption": ""}}])
    sync.run_incremental("secret", *paths)
    assert json.loads(paths[0].read_text()) == []


@pytest.mark.parametrize("source", ["public", "bot"])
def test_deleted_nonprimary_album_member_requires_two_scans_then_removes_only_its_media(monkeypatch, tmp_path, source):
    paths = setup_files(tmp_path)
    monkeypatch.setattr(sync, "scan_public_history", lambda: [public_album()])
    monkeypatch.setattr(sync, "download_public_media", lambda url: picture("red" if "a.jpg" in url else "blue"))
    monkeypatch.setattr(sync, "download_bot_media", lambda token, file_id: picture("red" if file_id == "p1" else "blue"))
    messages = [{"message_id": mid, "date": 100, "chat": {"username": "sarawakpropertyguru"},
                 "caption": "House For Sale RM500,000" if mid == 20 else "", "media_group_id": "album-a",
                 "photo": [{"file_id": fid}]} for mid, fid in [(20, "p1"), (21, "p2")]]
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: [{"update_id": 5 + i, "channel_post": message} for i, message in enumerate(messages)])
    sync.run_bootstrap(*paths) if source == "public" else sync.run_incremental("secret", *paths)
    first_asset, second_asset = paths[2] / "20-0.webp", paths[2] / "20-1.webp"
    before = first_asset.read_bytes()
    incomplete = {**public_album(), "message_ids": [20], "media_urls": ["https://cdn1.cdn-telegram.org/a.jpg"],
                  "public_album_members": {"20": ["https://cdn1.cdn-telegram.org/a.jpg"]}}
    monkeypatch.setattr(sync, "scan_public_history", lambda: [incomplete])
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: [])
    sync.run_incremental("secret", *paths)
    assert second_asset.exists(), "one scan cannot confirm a member deletion"
    assert len(json.loads(paths[0].read_text())[0]["images"]) == 2
    sync.run_incremental("secret", *paths)
    updated = json.loads(paths[0].read_text())
    assert len(updated) == 1
    assert updated[0]["images"] == ["assets/telegram/20-0.webp"]
    assert first_asset.read_bytes() == before
    assert not second_asset.exists()
    assert "21" not in updated[0].get("album_members", {})
    assert "21" not in updated[0].get("public_album_members", {})
    assert updated[0].get("file_ids", []) == (["p1"] if source == "bot" else [])


def test_deleted_album_member_with_only_photo_leaves_listing_without_stale_image(monkeypatch, tmp_path):
    album = {**public_album(), "media_urls": ["https://cdn1.cdn-telegram.org/b.jpg"],
             "public_album_members": {"21": ["https://cdn1.cdn-telegram.org/b.jpg"]}}
    paths = setup_files(tmp_path)
    monkeypatch.setattr(sync, "scan_public_history", lambda: [album])
    monkeypatch.setattr(sync, "download_public_media", lambda url: picture())
    monkeypatch.setattr(sync, "fetch_updates", lambda token, offset: [])
    sync.run_bootstrap(*paths)
    image = paths[2] / "20-0.webp"
    missing = {**album, "message_ids": [20], "media_urls": [], "public_album_members": {}}
    monkeypatch.setattr(sync, "scan_public_history", lambda: [missing])
    sync.run_incremental("secret", *paths)
    assert image.exists()
    sync.run_incremental("secret", *paths)
    updated = json.loads(paths[0].read_text())
    assert len(updated) == 1
    assert updated[0]["images"] == []
    assert not image.exists()

