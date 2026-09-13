import json
from pathlib import Path

from scripts.telegram_parser import is_listing, parse_listing


FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "property_posts.json").read_text(
        encoding="utf-8"
    )
)


def test_parses_rental_listing():
    item = parse_listing(FIXTURES["rental_listing"])

    assert item["id"] == "telegram-42"
    assert item["title"] == "🏭 Demak Laut Warehouse For RENT"
    assert item["status"] == "For Rent"
    assert item["price"] == "RM27,300/month"
    assert item["reference"] == "WC"
    assert item["facts"] == ["21,000 sq ft"]
    assert item["source_text"] == (
        "🏭 Demak Laut Warehouse For RENT RM27,300/month 21,000 sq ft Refer code: WC"
    )
    assert item["telegram_url"] == "https://t.me/sarawakpropertyguru/42"


def test_parses_malay_sale_link_listing_and_omits_absent_fields():
    item = parse_listing(FIXTURES["malay_sale_link_only"])

    assert item["id"] == "telegram-43"
    assert item["title"] == "Rumah Teres untuk dijual"
    assert item["status"] == "For Sale"
    assert item["price"] == "RM 450,000"
    assert item["source_text"] == (
        "Rumah Teres untuk dijual RM 450,000 More details: t.me/sarawakpropertyguru/43"
    )
    assert item["telegram_url"] == "https://t.me/sarawakpropertyguru/43"
    assert "facts" not in item
    assert "reference" not in item


def test_accepts_price_on_application_or_enquiry_with_property_intent():
    assert is_listing("Warehouse for rent — price on application", has_photo=True)
    assert is_listing("Condo for sale — enquire for price", has_photo=True)


def test_rejects_property_text_without_photo_or_telegram_link():
    assert not is_listing("House for sale RM 250,000", has_photo=False)


def test_rejects_announcement_without_property_intent():
    assert parse_listing(FIXTURES["announcement"]) is None
