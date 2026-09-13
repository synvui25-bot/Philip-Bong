import re


PROPERTY_TERMS = re.compile(
    r"\b(for sale|for rent|rental|selling|house|condo(?:minium)?|shop\s?lot|warehouse|land|rumah|untuk dijual|untuk disewa)\b",
    re.I,
)
PRICE = re.compile(r"\bRM\s?[\d,.]+(?:\s*(?:/|per)\s*(?:month|bulan|psf))?", re.I)
SIZE = re.compile(r"\b(?:approx\.?\s*)?[\d,.]+\s*(?:sq\s*ft|sqft|ft²|acres?)\b", re.I)
REFERENCE = re.compile(r"(?:refer(?:ence)?\s*code|ref)\s*:\s*([\w-]+)", re.I)


def is_listing(text: str, has_photo: bool) -> bool:
    has_price = bool(
        PRICE.search(text)
        or re.search(r"price\s+on\s+application|enquire\s+for\s+price", text, re.I)
    )
    return bool(PROPERTY_TERMS.search(text) and has_price and (has_photo or "t.me/" in text))


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def _status(text: str) -> str | None:
    if re.search(r"\b(for rent|rental|untuk disewa)\b", text, re.I):
        return "For Rent"
    if re.search(r"\b(for sale|selling|untuk dijual)\b", text, re.I):
        return "For Sale"
    return None


def parse_listing(post: dict) -> dict | None:
    text = post.get("text") or post.get("caption") or ""
    if not isinstance(text, str) or not is_listing(text, bool(post.get("photo"))):
        return None

    lines = [_normalize_whitespace(line) for line in text.splitlines()]
    title = next((line for line in lines if line), "")
    source_text = _normalize_whitespace(text)
    message_id = post["message_id"]

    item = {
        "id": f"telegram-{message_id}",
        "title": title,
        "source_text": source_text,
        "telegram_url": f"https://t.me/sarawakpropertyguru/{message_id}",
    }

    status = _status(source_text)
    if status:
        item["status"] = status

    price = PRICE.search(source_text)
    if price:
        item["price"] = price.group(0)

    facts = [match.group(0) for match in SIZE.finditer(source_text)]
    if facts:
        item["facts"] = facts

    reference = REFERENCE.search(source_text)
    if reference:
        item["reference"] = reference.group(1)

    return item
