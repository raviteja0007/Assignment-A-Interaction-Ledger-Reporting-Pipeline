"""
Unit tests for data normalization and schema validation rules.
"""
from datetime import datetime, timezone
from solution.config import IST
from solution.normalizer import (
    build_header_mapping,
    normalize_header,
    normalize_phone,
    parse_date,
    parse_decimal,
    parse_enum,
    parse_timestamp,
)


def test_normalize_header():
    assert normalize_header("Insured Name") == "insured name"
    assert normalize_header("  policy_number  ") == "policy number"
    assert normalize_header("Mobile__No") == "mobile no"
    assert normalize_header("agent_notes") == "agent notes"


def test_build_header_mapping():
    fields = [
        {"name": "customer_name", "aliases": ["name", "customer name", "insured name"]},
        {"name": "phone", "aliases": ["mobile", "mobile no", "contact"]},
        {"name": "policy_number", "aliases": ["policy no", "policy"]},
    ]
    raw_headers = ["Insured Name", "Mobile No", "Policy No", "random_extra"]
    mapping = build_header_mapping(raw_headers, fields)
    assert mapping == {
        "Insured Name": "customer_name",
        "Mobile No": "phone",
        "Policy No": "policy_number",
    }


def test_normalize_phone():
    # Valid Indian mobile numbers
    assert normalize_phone("+919876543210") == "+919876543210"
    assert normalize_phone("9876543210") == "+919876543210"
    assert normalize_phone("09876543210") == "+919876543210"
    assert normalize_phone("+91 98765 43210") == "+919876543210"
    assert normalize_phone("91-98765-43210") == "+919876543210"
    assert normalize_phone("(+91) 9876543210") == "+919876543210"
    assert normalize_phone("6123456789") == "+916123456789"
    assert normalize_phone("7123456789") == "+917123456789"
    assert normalize_phone("8123456789") == "+918123456789"

    # Invalid numbers
    assert normalize_phone("5123456789") is None  # Starts with 5
    assert normalize_phone("987654321") is None   # 9 digits
    assert normalize_phone("987654321012") is None # Too long
    assert normalize_phone("022-2654-7890") is None # Landline starting with 2
    assert normalize_phone("N/A") is None
    assert normalize_phone("+1 415 555 0134") is None


def test_parse_date():
    # Supported formats
    assert parse_date("2026-11-21") == "2026-11-21"
    assert parse_date("27/10/2026") == "2026-10-27"
    assert parse_date("17-12-2026") == "2026-12-17"
    assert parse_date("29-Dec-2026") == "2026-12-29"
    assert parse_date("01-dec-2026") == "2026-12-01"

    # Invalid / impossible dates
    assert parse_date("31/02/2026") is None
    assert parse_date("2026-13-01") is None
    assert parse_date("soon") is None
    assert parse_date("TBD") is None


def test_parse_decimal():
    assert parse_decimal("₹27,554") == 27554.00
    assert parse_decimal("Rs. 31629") == 31629.00
    assert parse_decimal("INR 25301.4") == 25301.40
    assert parse_decimal("6850") == 6850.00
    assert parse_decimal("4985.756") == 4985.76
    assert parse_decimal("-100") is None
    assert parse_decimal("N/A") is None
    assert parse_decimal("twelve thousand") is None


def test_parse_enum():
    allowed = ["en", "hi", "ta", "kn", "te", "mr"]
    assert parse_enum("en", allowed) == "en"
    assert parse_enum("HI", allowed) == "hi"
    assert parse_enum(" ta ", allowed) == "ta"
    assert parse_enum("Hindi", allowed) is None
    assert parse_enum("english", allowed) is None


def test_parse_timestamp():
    # UTC
    utc_ts = "2026-10-01T05:26:10Z"
    dt = parse_timestamp(utc_ts)
    assert dt.tzinfo == IST
    assert dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-10-01 10:56:10"

    # Offset IST
    offset_ts = "2026-10-01T10:40:19+05:30"
    dt = parse_timestamp(offset_ts)
    assert dt.tzinfo == IST
    assert dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-10-01 10:40:19"

    # Naive means IST
    naive_ts = "2026-10-01 09:15:55"
    dt = parse_timestamp(naive_ts)
    assert dt.tzinfo == IST
    assert dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-10-01 09:15:55"

