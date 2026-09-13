import json
from pathlib import Path
import pytest

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
        "🏭 Demak Laut Warehouse For RENT\nRM27,300/month\n21,000 sq ft\nRefer code: WC"
    )
    assert item["telegram_url"] == "https://t.me/sarawakpropertyguru/42"


def test_parses_malay_sale_link_listing_and_omits_absent_fields():
    item = parse_listing(FIXTURES["malay_sale_link_only"])

    assert item["id"] == "telegram-43"
    assert item["title"] == "Rumah Teres untuk dijual"
    assert item["status"] == "For Sale"
    assert item["price"] == "RM 450,000"
    assert item["source_text"] == (
        "Rumah Teres untuk dijual  \nRM 450,000\nMore details: t.me/sarawakpropertyguru/43"
    )
    assert item["telegram_url"] == "https://t.me/sarawakpropertyguru/43"
    assert "facts" not in item
    assert "reference" not in item


def test_accepts_price_on_application_or_enquiry_with_property_intent():
    assert is_listing("Warehouse for rent — price on application", has_photo=True)
    assert is_listing("Condo for sale — enquire for price", has_photo=True)


def test_rejects_property_text_without_photo_or_telegram_link():
    assert not is_listing("House for sale RM 250,000", has_photo=False)


def test_accepts_verified_public_channel_post_without_downloadable_photo():
    item = parse_listing({
        "message_id": 52,
        "text": "Kuching House For Sale\nRM 250,000",
        "photo": False,
        "telegram_url": "https://t.me/sarawakpropertyguru/52",
    })

    assert item is not None
    assert item["id"] == "telegram-52"


def test_rejects_announcement_without_property_intent():
    assert parse_listing(FIXTURES["announcement"]) is None


@pytest.mark.parametrize("fixture,expected", [
    ("english_structured", {"location": "Tabuan Jaya, Kuching", "property_type": "Semi-detached house",
                            "bedrooms": 4, "bathrooms": 3, "parking": 2, "negotiable": True, "edited_at": 1789092600}),
    ("malay_structured", {"location": "Kota Samarahan", "property_type": "Rumah teres",
                          "bedrooms": 3, "bathrooms": 2, "parking": 1, "negotiable": False, "edited_at": 1789092660}),
])
def test_extracts_only_explicit_english_and_malay_fields(fixture, expected):
    listing = parse_listing(FIXTURES[fixture])
    assert {field: listing.get(field) for field in expected} == expected


@pytest.mark.parametrize("phrase,expected", [("boleh runding", True), ("tidak boleh runding", False),
                                             ("non-negotiable", False), ("not negotiable", False)])
def test_negotiability_respects_explicit_negation(phrase, expected):
    listing = parse_listing({**FIXTURES["rental_listing"], "caption": "Kuching House For Sale RM500,000 " + phrase})
    assert listing.get("negotiable") is expected


@pytest.mark.parametrize("phrase", ["Negotiable: No", "tak boleh runding", "not currently negotiable"])
def test_negotiability_recognizes_explicit_negative_phrases(phrase):
    listing = parse_listing({"message_id": 91, "caption": f"House For Sale RM500,000 {phrase}", "photo": [{}]})
    assert listing.get("negotiable") is False


def test_negotiability_omits_ambiguous_question():
    listing = parse_listing({"message_id": 92, "caption": "House For Sale RM500,000 negotiable?", "photo": [{}]})
    assert "negotiable" not in listing


@pytest.mark.parametrize("edit_date", [None, True, -1, 0, "yesterday", float("inf")])
def test_missing_or_ambiguous_fields_and_invalid_edit_time_are_omitted(edit_date):
    listing = parse_listing({**FIXTURES["rental_listing"], "edit_date": edit_date,
                             "caption": "Kuching House For Sale RM500,000\nNear 3 schools, 2 shops; call for details"})
    assert not ({"location", "property_type", "bedrooms", "bathrooms", "parking", "negotiable", "edited_at"} & listing.keys())

