import math
import re


PROPERTY_TERMS = re.compile(
    r"\b(for sale|for rent|rental|selling|house|condo(?:minium)?|shop\s?lot|warehouse|land|rumah|untuk dijual|untuk disewa)\b",
    re.I,
)
PRICE = re.compile(r"\bRM\s?[\d,.]+(?:\s*(?:/|per)\s*(?:month|bulan|psf))?", re.I)
SIZE = re.compile(r"\b(?:approx\.?\s*)?[\d,.]+k?\s*(?:sq\s*ft|sqft|ft²|acres?)\b", re.I)
REFERENCE = re.compile(r"(?:refer(?:ence)?\s*code|ref)\s*:\s*([\w-]+)", re.I)


def is_listing(text: str, has_photo: bool, has_source_link: bool = False) -> bool:
    has_price = bool(
        PRICE.search(text)
        or re.search(r"price\s+on\s+application|enquire\s+for\s+price", text, re.I)
    )
    return bool(PROPERTY_TERMS.search(text) and has_price
                and (has_photo or has_source_link or "t.me/" in text))


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
    message_id = post.get("message_id")
    canonical_url = f"https://t.me/sarawakpropertyguru/{message_id}"
    has_source_link = post.get("telegram_url") == canonical_url
    has_image = bool(post.get("photo") or post.get("file_ids"))
    if not isinstance(text, str) or not is_listing(text, has_image, has_source_link):
        return None

    lines = [_normalize_whitespace(line) for line in text.splitlines()]
    title = next((line for line in lines if line), "")
    source_text = text.strip()
    normalized_text = _normalize_whitespace(text)
    item = {
        "id": f"telegram-{message_id}",
        "title": title,
        "source_text": source_text,
        "telegram_url": canonical_url,
    }

    status = _status(normalized_text)
    if status:
        item["status"] = status

    price = PRICE.search(normalized_text)
    if price:
        item["price"] = price.group(0)

    facts = [match.group(0) for match in SIZE.finditer(normalized_text)]
    if facts:
        item["facts"] = facts

    reference = REFERENCE.search(normalized_text)
    if reference:
        item["reference"] = reference.group(1)

    # Only labeled values identify location/type. Counts must occupy an explicit
    # fact segment; nearby schools, ranges, and "3+1" rooms are not bedroom counts.
    for field, label in [("location", r"location|lokasi"),
                         ("property_type", r"property type|jenis hartanah")]:
        values = {match[1].strip() for line in lines
                  if (match := re.search(rf"(?:{label})\s*:\s*(.+)$", line, re.I))}
        if len(values) == 1:
            item[field] = values.pop()
    for field, label in [("bedrooms", r"bedrooms?|bilik tidur"),
                         ("bathrooms", r"bathrooms?|bilik air|bilik mandi"),
                         ("parking", r"car parks?|parking(?: spaces?)?|tempat letak kereta")]:
        values = set()
        for segment in re.split(r"[\n;|]", text):
            segment = segment.strip().lstrip("•").strip()
            match = re.fullmatch(rf"(?:{label})\s*:\s*(\d+)|(\d+)\s+(?:{label})", segment, re.I)
            if match:
                values.add(int(match[1] or match[2]))
        if len(values) == 1:
            item[field] = values.pop()
    negative_negotiability = re.search(
        r"\b(?:non[- ]negotiable|not(?:\s+\w+){0,2}\s+negotiable|"
        r"tidak boleh runding|tak boleh runding|harga tetap|fixed price|"
        r"negotiable\s*:\s*(?:no|false))\b",
        normalized_text,
        re.I,
    )
    positive_negotiability = re.search(
        r"\b(?:negotiable\s*:\s*(?:yes|true)|negotiable(?!\s*\?)|boleh runding)\b",
        normalized_text,
        re.I,
    )
    if negative_negotiability:
        item["negotiable"] = False
    elif positive_negotiability:
        item["negotiable"] = True
    edited_at = post.get("edit_date")
    if (isinstance(edited_at, (int, float)) and not isinstance(edited_at, bool)
            and math.isfinite(edited_at) and edited_at > 0):
        item["edited_at"] = edited_at

    return item

