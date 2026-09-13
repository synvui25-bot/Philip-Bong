from pathlib import Path

import pytest

from scripts import telegram_public as public


FIXTURE = (Path(__file__).parent / "fixtures/channel_preview.html").read_text(encoding="utf-8")


def terminal_page(message_id=10):
    return FIXTURE.split('<a class="tme_messages_more"')[0] + FIXTURE.split('</a>', 1)[1].replace(
        "sarawakpropertyguru/20", f"sarawakpropertyguru/{message_id}"
    ).replace("sarawakpropertyguru/21", f"sarawakpropertyguru/{message_id + 1}")


def test_semantic_history_parses_album_text_time_and_canonical_permalink():
    posts = public.parse_public_history(FIXTURE)
    assert [post["message_id"] for post in posts] == [20, 21]
    assert posts[0]["text"] == "Kuching House For Sale\nRM500,000 & negotiable 3 rooms"
    assert posts[0]["date"] == 1789041600
    assert posts[0]["telegram_url"] == "https://t.me/sarawakpropertyguru/20"
    assert posts[0]["media_urls"] == [
        "https://cdn1.cdn-telegram.org/a.jpg", "https://cdn1.cdn-telegram.org/b.jpg"
    ]


@pytest.mark.parametrize("html", [
    "<html>Temporary unavailable</html>",
    FIXTURE.replace("sarawakpropertyguru/20", "other_channel/20"),
    FIXTURE.replace('datetime="2026-09-10T12:00:00+00:00"', 'datetime="bad"'),
    FIXTURE[:FIXTURE.index('</section>')],
    FIXTURE.replace("https://cdn1.cdn-telegram.org/a.jpg", "http://127.0.0.1/private"),
])
def test_invalid_or_untrusted_history_is_not_a_successful_scan(html):
    with pytest.raises(public.PublicHistoryError):
        public.parse_public_history(html)


def test_full_scan_follows_only_older_channel_pagination():
    pages = {
        public.PUBLIC_URL: FIXTURE,
        public.PUBLIC_URL + "?before=20": terminal_page(),
    }
    posts = public.scan_public_history(fetch=pages.__getitem__)
    assert [post["message_id"] for post in posts] == [10, 11, 20, 21]


@pytest.mark.parametrize("href", ["https://evil.example/?before=20", "/s/other?before=20", "?before=20&extra=1", "?before=1"])
def test_scan_rejects_untrusted_pagination(href):
    html = FIXTURE.replace("/s/sarawakpropertyguru?before=20", href)
    with pytest.raises(public.PublicHistoryError):
        public.scan_public_history(fetch=lambda url: html)


def test_incomplete_scan_at_page_cap_and_stalled_cursor_fails():
    with pytest.raises(public.PublicHistoryError):
        public.scan_public_history(fetch=lambda url: FIXTURE, max_pages=1)
    with pytest.raises(public.PublicHistoryError):
        public.scan_public_history(fetch=lambda url: FIXTURE)


def test_temporary_fetch_failure_aborts_scan():
    def fetch(url):
        if "before" in url:
            raise OSError("timeout")
        return FIXTURE
    with pytest.raises(public.PublicHistoryError):
        public.scan_public_history(fetch=fetch)


def test_missing_post_requires_two_confirmations_without_mutating_input():
    listings = [{"id": "telegram-20", "message_id": 20}]
    original_state = {"last_update_id": 55}
    first, state = public.reconcile_missing(listings, set(), original_state)
    assert first == listings
    assert original_state == {"last_update_id": 55}
    assert state == {"last_update_id": 55, "missing_counts": {"20": 1}}
    second, state = public.reconcile_missing(first, set(), state)
    assert second == []
    assert state["missing_counts"] == {}


def test_visible_post_resets_missing_count():
    listings = [{"id": "telegram-20", "message_id": 20}]
    kept, state = public.reconcile_missing(listings, {20}, {"missing_counts": {"20": 1}})
    assert kept == listings
    assert "20" not in state["missing_counts"]


def test_public_album_retains_all_linked_message_ids():
    html = terminal_page().replace("sarawakpropertyguru/10?single", "sarawakpropertyguru/12?single")
    assert public.parse_public_history(html)[0]["message_ids"] == [10, 12]


def test_photo_album_single_permalink_is_accepted():
    posts = public.parse_public_history(FIXTURE)
    assert posts[0]["message_ids"] == [20]
    assert len(posts[0]["media_urls"]) == 2


@pytest.mark.parametrize("url", [
    "https://foreign.example/sarawakpropertyguru/20?single",
    "https://t.me/other_channel/20?single",
    "https://t.me/sarawakpropertyguru/20?single&before=1",
    "https://t.me/sarawakpropertyguru/20?single=anything",
    "https://t.me/sarawakpropertyguru/20?single&single",
    "https://t.me/sarawakpropertyguru/20?unrelated",
])
def test_album_single_permalink_does_not_allow_other_hosts_channels_or_queries(url):
    with pytest.raises(public.PublicHistoryError):
        public.parse_public_history(FIXTURE.replace("https://t.me/sarawakpropertyguru/20?single", url))


def test_pagination_cannot_skip_an_unscanned_id_range():
    pages = {public.PUBLIC_URL: FIXTURE.replace("before=20", "before=19"),
             public.PUBLIC_URL + "?before=19": terminal_page()}
    with pytest.raises(public.PublicHistoryError):
        public.scan_public_history(fetch=pages.__getitem__)


def test_default_scan_never_fetches_more_than_100_pages():
    requests = []
    def fetch(url):
        requests.append(url)
        oldest = 2000 - 2 * len(requests)
        return FIXTURE.replace("sarawakpropertyguru/20", f"sarawakpropertyguru/{oldest}").replace(
            "sarawakpropertyguru/21", f"sarawakpropertyguru/{oldest + 1}"
        ).replace("before=20", f"before={oldest}")
    with pytest.raises(public.PublicHistoryError, match="page limit"):
        public.scan_public_history(fetch=fetch)
    assert len(requests) == 100
