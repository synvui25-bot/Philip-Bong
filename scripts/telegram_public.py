"""Strict public-channel preview parsing and complete-scan reconciliation."""

from datetime import datetime
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from scripts.telegram_media import MediaError, validate_public_media_url


PUBLIC_URL = "https://t.me/s/sarawakpropertyguru"
MAX_HTML_BYTES = 5 * 1024 * 1024


class PublicHistoryError(RuntimeError):
    """A public scan is incomplete and must not authorize data replacement."""


class _Node:
    def __init__(self, tag="", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def has_class(self, name):
        return name in self.attrs.get("class", "").split()

    def descendants(self):
        for child in self.children:
            if isinstance(child, _Node):
                yield child
                yield from child.descendants()

    def text(self):
        if self.tag in {"script", "style"}:
            return ""
        if self.tag == "br":
            return "\n"
        return "".join(child.text() if isinstance(child, _Node) else child for child in self.children)


class _PreviewParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if len(self.stack) == 1 or self.stack[-1].tag != tag:
            raise PublicHistoryError("malformed public preview HTML")
        self.stack.pop()

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _parse_page(html: str) -> tuple[list[dict], list[str]]:
    parser = _PreviewParser()
    try:
        parser.feed(html)
        parser.close()
        if len(parser.stack) != 1:
            raise PublicHistoryError("truncated public preview HTML")
        histories = [node for node in parser.root.descendants() if node.has_class("tgme_channel_history")]
        if len(histories) != 1:
            raise PublicHistoryError("public preview history is missing")
        nodes = list(histories[0].descendants())
        posts = []
        for node in nodes:
            if not node.has_class("tgme_widget_message"):
                continue
            match = re.fullmatch(r"sarawakpropertyguru/([1-9][0-9]*)", node.attrs.get("data-post", ""))
            if not match:
                raise PublicHistoryError("only @sarawakpropertyguru history is accepted")
            message_id = int(match[1])
            children = list(node.descendants())
            date_links = [child for child in children if child.has_class("tgme_widget_message_date")]
            permalink = f"https://t.me/sarawakpropertyguru/{message_id}"
            if len(date_links) != 1 or date_links[0].attrs.get("href") != permalink:
                raise PublicHistoryError("invalid public post permalink")
            times = [child for child in date_links[0].descendants() if child.tag == "time"]
            if len(times) != 1:
                raise PublicHistoryError("missing public post timestamp")
            timestamp = datetime.fromisoformat(times[0].attrs["datetime"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None or timestamp.timestamp() <= 0:
                raise PublicHistoryError("invalid public post timestamp")
            text_nodes = [child for child in children if child.has_class("tgme_widget_message_text")]
            media = []
            message_ids = {message_id}
            for child in children:
                if child.has_class("tgme_widget_message_photo_wrap"):
                    if child.attrs.get("href"):
                        link = urlsplit(child.attrs["href"])
                        member = re.fullmatch(r"/sarawakpropertyguru/([1-9][0-9]*)", link.path)
                        query = parse_qs(link.query, keep_blank_values=True)
                        if (link.scheme != "https" or link.netloc != "t.me" or not member
                                or link.fragment or query not in ({}, {"single": [""]})):
                            raise PublicHistoryError("invalid public album permalink")
                        message_ids.add(int(member[1]))
                    style = child.attrs.get("style", "")
                    image = re.search(r"background-image\s*:\s*url\(\s*(['\"]?)(.*?)\1\s*\)", style)
                    if not image:
                        raise PublicHistoryError("public post image URL is missing")
                    media.append(validate_public_media_url(image[2]))
            posts.append({
                "message_id": message_id,
                "message_ids": sorted(message_ids),
                "chat": {"username": "sarawakpropertyguru"},
                "date": int(timestamp.timestamp()),
                "text": "\n".join(part.text() for part in text_nodes).strip(),
                "media_urls": list(dict.fromkeys(media)),
                "photo": bool(media),
                "telegram_url": permalink,
            })
        if not posts:
            raise PublicHistoryError("empty public preview cannot confirm deletions")
        if len({post["message_id"] for post in posts}) != len(posts):
            raise PublicHistoryError("duplicate public post ID")
        links = [node.attrs.get("href", "") for node in nodes if node.has_class("tme_messages_more")]
        return sorted(posts, key=lambda post: post["message_id"]), links
    except PublicHistoryError:
        raise
    except (KeyError, TypeError, ValueError, MediaError, RecursionError):
        raise PublicHistoryError("invalid public preview data") from None


def parse_public_history(html: str) -> list[dict]:
    return _parse_page(html)[0]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PublicHistoryError("public preview redirect refused")


def _fetch_html(url: str) -> str:
    with build_opener(_NoRedirect()).open(Request(url), timeout=30) as response:
        if response.headers.get_content_type() != "text/html":
            raise PublicHistoryError("unexpected public preview content type")
        content = response.read(MAX_HTML_BYTES + 1)
    if len(content) > MAX_HTML_BYTES:
        raise PublicHistoryError("public preview exceeds size limit")
    return content.decode("utf-8")


def scan_public_history(*, fetch=None, max_pages: int = 100) -> list[dict]:
    """Return all visible posts, or fail without authorizing reconciliation."""
    if not 1 <= max_pages <= 100:
        raise PublicHistoryError("public scan page limit must be between 1 and 100")
    fetch = fetch or _fetch_html
    url = PUBLIC_URL
    before = None
    by_id = {}
    try:
        for _ in range(max_pages):
            posts, links = _parse_page(fetch(url))
            lowest = min(post["message_id"] for post in posts)
            if before is not None and lowest >= before:
                raise PublicHistoryError("public pagination did not progress")
            for post in posts:
                by_id.setdefault(post["message_id"], post)
            if not links:
                return [by_id[key] for key in sorted(by_id)]
            cursors = []
            for link in links:
                parsed = urlsplit(urljoin(PUBLIC_URL, link))
                query = parse_qs(parsed.query, keep_blank_values=True)
                if (parsed.scheme != "https" or parsed.netloc != "t.me"
                        or parsed.path != "/s/sarawakpropertyguru" or parsed.fragment
                        or set(query) != {"before"} or len(query["before"]) != 1
                        or not re.fullmatch(r"[1-9][0-9]*", query["before"][0])):
                    raise PublicHistoryError("untrusted public pagination link")
                cursor = int(query["before"][0])
                if cursor != lowest or (before is not None and cursor >= before):
                    raise PublicHistoryError("public pagination did not progress")
                cursors.append(cursor)
            before = min(cursors)
            url = f"{PUBLIC_URL}?before={before}"
        raise PublicHistoryError("public scan reached page limit before completion")
    except PublicHistoryError:
        raise
    except Exception:
        raise PublicHistoryError("public preview fetch failed") from None


def reconcile_missing(listings: list[dict], visible_ids: set[int], state: dict) -> tuple[list[dict], dict]:
    """Caller must supply IDs from a successful, complete public scan only."""
    old_counts = state.get("missing_counts", {})
    counts = {}
    kept = []
    for listing in listings:
        message_id = listing["message_id"]
        if message_id in visible_ids:
            kept.append(dict(listing))
            continue
        key = str(message_id)
        count = old_counts.get(key, 0) + 1
        if count < 2:
            counts[key] = count
            kept.append(dict(listing))
    return kept, {**state, "missing_counts": counts}
