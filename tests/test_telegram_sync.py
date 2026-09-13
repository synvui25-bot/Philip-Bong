import pytest

from scripts.sync_telegram import apply_updates, group_media_posts, run_incremental


def listing_message(message_id, caption, *, date=1, media_group_id=None, file_id="p1"):
    message = {
        "message_id": message_id,
        "date": date,
        "chat": {"username": "sarawakpropertyguru"},
        "caption": caption,
        "photo": [{"file_id": file_id}],
    }
    if media_group_id:
        message["media_group_id"] = media_group_id
    return message


def test_album_becomes_one_listing_with_caption_message_as_stable_record():
    messages = [
        listing_message(10, "House For Sale RM500,000", media_group_id="a", file_id="p1"),
        {
            "message_id": 11,
            "date": 2,
            "chat": {"username": "sarawakpropertyguru"},
            "media_group_id": "a",
            "photo": [{"file_id": "p2"}],
        },
    ]

    grouped = group_media_posts(messages)

    assert len(grouped) == 1
    assert grouped[0]["message_id"] == 10
    assert grouped[0]["file_ids"] == ["p1", "p2"]


def test_album_ignores_duplicate_photo_file_ids():
    messages = [
        listing_message(10, "House For Sale RM500,000", media_group_id="a", file_id="p1"),
        {
            "message_id": 11,
            "date": 2,
            "chat": {"username": "sarawakpropertyguru"},
            "media_group_id": "a",
            "photo": [{"file_id": "p1"}],
        },
    ]

    grouped = group_media_posts(messages)

    assert grouped[0]["file_ids"] == ["p1"]


def test_edit_replaces_existing_record_and_advances_offset():
    current = [{"id": "telegram-10", "price": "RM500,000"}]
    updates = [
        {
            "update_id": 5,
            "edited_channel_post": listing_message(10, "House For Sale RM480,000"),
        }
    ]

    listings, offset = apply_updates(current, updates)

    assert listings[0]["price"] == "RM480,000"
    assert listings[0]["message_id"] == 10
    assert offset == 6


def test_later_same_member_snapshot_replaces_caption_and_photo():
    updates = [
        {
            "update_id": 5,
            "channel_post": listing_message(
                10,
                "House For Sale RM500,000",
                media_group_id="a",
                file_id="p1",
            ),
        },
        {
            "update_id": 6,
            "edited_channel_post": listing_message(
                10,
                "House For Sale RM480,000",
                media_group_id="a",
                file_id="p2",
            ),
        },
    ]

    listings, offset = apply_updates([], updates)

    assert listings[0]["price"] == "RM480,000"
    assert listings[0]["file_ids"] == ["p2"]
    assert offset == 7


@pytest.mark.parametrize("stored", [False, True])
def test_latest_empty_album_caption_clears_listing_in_same_batch(stored):
    initial = listing_message(20, "House For Sale RM500,000", media_group_id="a")
    current, _ = apply_updates([], [{"update_id": 1, "channel_post": initial}]) if stored else ([], 0)
    updates = [
        {"update_id": 5, "edited_channel_post": initial},
        {"update_id": 6, "edited_channel_post": {**initial, "caption": ""}},
        {"update_id": 7, "channel_post": listing_message(21, "", media_group_id="a", file_id="p2")},
    ]
    listings, offset = apply_updates(current, updates)
    assert listings == []
    assert offset == 8


def test_same_batch_photo_replacement_preserves_only_latest_member_and_other_members():
    updates = [
        {"update_id": 5, "channel_post": listing_message(20, "House For Sale RM500,000", media_group_id="a")},
        {"update_id": 6, "channel_post": listing_message(21, "", media_group_id="a", file_id="p2")},
        {"update_id": 7, "edited_channel_post": listing_message(20, "House For Sale RM480,000", media_group_id="a", file_id="p3")},
    ]
    listings, _ = apply_updates([], list(reversed(updates)))
    assert listings[0]["file_ids"] == ["p3", "p2"]
    assert listings[0]["album_members"] == {"20": ["p3"], "21": ["p2"]}


def test_noncaption_album_edit_updates_explicit_edit_time():
    initial = listing_message(20, "House For Sale RM500,000", media_group_id="a")
    current, _ = apply_updates([], [{"update_id": 1, "channel_post": initial}])
    edited = {**listing_message(21, "", media_group_id="a", file_id="p2"), "edit_date": 200}
    listings, _ = apply_updates(current, [{"update_id": 2, "edited_channel_post": edited}])
    assert listings[0].get("edited_at") == 200
    assert listings[0]["price"] == "RM500,000"


def test_new_listing_includes_album_photos_and_sorts_newest_first():
    updates = [
        {
            "update_id": 7,
            "channel_post": listing_message(20, "House For Sale RM700,000", date=10),
        },
        {
            "update_id": 8,
            "channel_post": listing_message(21, "Warehouse For Rent RM3,000/month", date=20),
        },
    ]

    listings, offset = apply_updates([], updates)

    assert [listing["id"] for listing in listings] == ["telegram-21", "telegram-20"]
    assert listings[0]["published_at"] == 20
    assert listings[0]["file_ids"] == ["p1"]
    assert offset == 9


@pytest.mark.parametrize("date", [None, "1700000000", 0, -1, True])
def test_qualifying_post_without_positive_numeric_date_is_skipped(date):
    message = listing_message(10, "House For Sale RM500,000")
    if date is None:
        message.pop("date")
    else:
        message["date"] = date

    listings, offset = apply_updates([], [{"update_id": 5, "channel_post": message}])

    assert listings == []
    assert offset == 6


def test_duplicate_update_id_is_ignored():
    message = listing_message(10, "House For Sale RM500,000")
    updates = [
        {"update_id": 5, "channel_post": message},
        {"update_id": 5, "edited_channel_post": listing_message(10, "House For Sale RM1")},
    ]

    listings, offset = apply_updates([], updates)

    assert listings[0]["price"] == "RM500,000"
    assert offset == 6


def test_non_listing_edit_removes_existing_record():
    current = [{"id": "telegram-10", "price": "RM500,000"}]
    updates = [
        {
            "update_id": 5,
            "edited_channel_post": {
                "message_id": 10,
                "date": 1,
                "chat": {"username": "sarawakpropertyguru"},
                "caption": "Happy Malaysia Day!",
            },
        }
    ]

    listings, offset = apply_updates(current, updates)

    assert listings == []
    assert offset == 6


def test_nonnumeric_date_non_listing_edit_removes_existing_record():
    current = [{"id": "telegram-10", "price": "RM500,000"}]
    updates = [
        {
            "update_id": 5,
            "edited_channel_post": {
                "message_id": 10,
                "chat": {"username": "sarawakpropertyguru"},
                "caption": "Happy Malaysia Day!",
            },
        }
    ]

    listings, offset = apply_updates(current, updates)

    assert listings == []
    assert offset == 6


def test_rejects_updates_from_another_channel_without_advancing_offset():
    update = {
        "update_id": 5,
        "channel_post": {
            **listing_message(10, "House For Sale RM500,000"),
            "chat": {"username": "another_channel"},
        },
    }

    with pytest.raises(ValueError, match="sarawakpropertyguru"):
        apply_updates([], [update])


def test_fetch_updates_returns_bot_result(monkeypatch):
    from scripts import telegram_client

    payload = {"ok": True, "result": [{"update_id": 5}]}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(telegram_client, "urlopen", lambda request, timeout: Response())
    monkeypatch.setattr(telegram_client.json, "load", lambda response: payload)

    assert telegram_client.fetch_updates("secret-token", 5) == payload["result"]


def test_fetch_updates_raises_for_api_error(monkeypatch):
    from scripts import telegram_client

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(telegram_client, "urlopen", lambda request, timeout: Response())
    monkeypatch.setattr(
        telegram_client.json,
        "load",
        lambda response: {"ok": False, "description": "Unauthorized"},
    )

    with pytest.raises(telegram_client.TelegramError, match="Unauthorized"):
        telegram_client.fetch_updates("secret-token", 5)


def test_incremental_sync_uses_saved_offset_and_persists_after_processing(
    monkeypatch, tmp_path
):
    listings_path = tmp_path / "telegram-listings.json"
    state_path = tmp_path / "telegram-sync-state.json"
    listings_path.write_text("[]\n", encoding="utf-8")
    state_path.write_text('{"last_update_id": 5}\n', encoding="utf-8")
    update = {
        "update_id": 5,
        "channel_post": listing_message(10, "House For Sale RM500,000"),
    }
    monkeypatch.setattr("scripts.sync_telegram.fetch_updates", lambda token, offset: [update])
    monkeypatch.setattr("scripts.sync_telegram.scan_public_history", lambda: [{"message_id": 10}])
    monkeypatch.setattr("scripts.sync_telegram.download_bot_media", lambda token, file_id: b"unavailable image")

    listings, offset = run_incremental("secret-token", listings_path, state_path, tmp_path / "assets/telegram")

    assert listings[0]["id"] == "telegram-10"
    assert offset == 6
    assert state_path.read_text(encoding="utf-8") == '{\n  "last_update_id": 6,\n  "missing_counts": {}\n}\n'


def test_later_non_caption_member_edit_changes_only_its_photo():
    initial = [
        {"update_id": 5, "channel_post": listing_message(20, "House For Sale RM500,000", media_group_id="a", file_id="p1")},
        {"update_id": 6, "channel_post": listing_message(21, "", media_group_id="a", file_id="p2")},
    ]
    stored, _ = apply_updates([], initial)
    edited, _ = apply_updates(stored, [{"update_id": 7, "edited_channel_post": listing_message(21, "", media_group_id="a", file_id="p3")}])
    assert len(edited) == 1
    assert edited[0]["message_id"] == 20
    assert edited[0]["price"] == "RM500,000"
    assert edited[0]["file_ids"] == ["p1", "p3"]


def test_later_album_member_joins_existing_listing_without_caption():
    stored, _ = apply_updates([], [{"update_id": 5, "channel_post": listing_message(20, "House For Sale RM500,000", media_group_id="a", file_id="p1")}])
    updated, _ = apply_updates(stored, [{"update_id": 6, "channel_post": listing_message(21, "", media_group_id="a", file_id="p2")}])
    assert len(updated) == 1
    assert updated[0]["file_ids"] == ["p1", "p2"]


def test_a_different_media_group_replaces_previous_album_members():
    stored, _ = apply_updates([], [
        {"update_id": 5, "channel_post": listing_message(20, "House For Sale RM500,000", media_group_id="a", file_id="p1")},
        {"update_id": 6, "channel_post": listing_message(21, "", media_group_id="a", file_id="p2")},
    ])
    updated, _ = apply_updates(stored, [{"update_id": 7, "edited_channel_post": listing_message(20, "House For Sale RM480,000", media_group_id="replacement", file_id="p3")}])
    assert updated[0]["file_ids"] == ["p3"]
    assert updated[0]["price"] == "RM480,000"


def test_album_caption_edit_to_non_listing_removes_record():
    stored, _ = apply_updates([], [
        {"update_id": 5, "channel_post": listing_message(20, "House For Sale RM500,000", media_group_id="a", file_id="p1")},
        {"update_id": 6, "channel_post": listing_message(21, "", media_group_id="a", file_id="p2")},
    ])
    updated, _ = apply_updates(stored, [{"update_id": 7, "edited_channel_post": listing_message(20, "Happy Malaysia Day", media_group_id="a", file_id="p1")}])
    assert updated == []


def test_legacy_album_retains_order_when_caption_is_second_photo():
    legacy = {"id": "telegram-21", "message_id": 21, "published_at": 100,
              "file_ids": ["p1", "p2"], "price": "RM500,000"}
    migrated, _ = apply_updates([legacy], [{"update_id": 5, "edited_channel_post": listing_message(21, "House For Sale RM480,000", media_group_id="a", file_id="p2")}])
    assert migrated[0]["file_ids"] == ["p1", "p2"]
    updated, _ = apply_updates(migrated, [{"update_id": 6, "edited_channel_post": listing_message(21, "House For Sale RM480,000", media_group_id="a", file_id="p3")}])
    assert updated[0]["file_ids"] == ["p1", "p3"]


def test_legacy_unknown_photo_can_be_claimed_by_later_member_and_replaced():
    legacy = {"id": "telegram-20", "message_id": 20, "published_at": 100,
              "file_ids": ["p1", "p2"], "price": "RM500,000"}
    migrated, _ = apply_updates([legacy], [{"update_id": 5, "edited_channel_post": listing_message(20, "House For Sale RM480,000", media_group_id="a", file_id="p1")}])
    claimed, _ = apply_updates(migrated, [{"update_id": 6, "edited_channel_post": listing_message(21, "", media_group_id="a", file_id="p2")}])
    updated, _ = apply_updates(claimed, [{"update_id": 7, "edited_channel_post": listing_message(21, "", media_group_id="a", file_id="p3")}])
    assert len(updated) == 1
    assert updated[0]["file_ids"] == ["p1", "p3"]


def test_first_legacy_photo_replacement_does_not_guess_which_unknown_file_to_remove():
    legacy = {"id": "telegram-20", "message_id": 20, "published_at": 100,
              "file_ids": ["p1", "p2"], "price": "RM500,000"}
    migrated, _ = apply_updates([legacy], [{"update_id": 5, "edited_channel_post": listing_message(20, "House For Sale RM480,000", media_group_id="a", file_id="p3")}])
    assert migrated[0]["file_ids"] == ["p1", "p2", "p3"]

