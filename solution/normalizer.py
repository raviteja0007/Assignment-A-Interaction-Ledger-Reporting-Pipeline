"""
Validation, normalization, and header matching utilities according to spec.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from solution.config import IST


def normalize_header(header: str) -> str:
    """
    To normalise a header: lower-case it, trim it, and replace every run of
    '_' or whitespace with a single space.
    """
    cleaned = header.strip().lower()
    return re.sub(r"[\s_]+", " ", cleaned)


def build_header_mapping(csv_headers: list[str], schema_fields: list[dict]) -> dict[str, str]:
    """
    Maps CSV header strings to schema field names based on normalized names and aliases.
    Unmatched columns are ignored.
    """
    mapping = {}
    normalized_csv_headers = {h: normalize_header(h) for h in csv_headers}

    for orig_header, norm_h in normalized_csv_headers.items():
        matched_field_name = None
        for field in schema_fields:
            name_norm = normalize_header(field["name"])
            aliases_norm = [normalize_header(a) for a in field.get("aliases", [])]
            if norm_h == name_norm or norm_h in aliases_norm:
                matched_field_name = field["name"]
                break
        if matched_field_name:
            mapping[orig_header] = matched_field_name

    return mapping


def normalize_phone(val: str | None) -> str | None:
    """
    Normalizes an Indian mobile phone number:
    - Strip every non-digit.
    - If 12 digits starting with 91, drop 91.
    - If 11 digits starting with 0, drop 0.
    - Valid only if exactly 10 digits and starts with 6, 7, 8 or 9.
    - Canonical storage: +91XXXXXXXXXX.
    """
    if not val:
        return None
    digits = re.sub(r"\D", "", str(val))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"
    return None


def parse_date(val: str | None) -> str | None:
    """
    Parses date accepting YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, and DD-Mon-YYYY.
    Dates are day-first. Impossible dates (e.g. 31/02) return None.
    Stores canonically as YYYY-MM-DD.
    """
    if not val:
        return None
    s = str(val).strip()
    if not s:
        return None

    # Supported formats per spec
    formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d-%b-%Y",
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(s, fmt)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def parse_decimal(val: str | None) -> float | None:
    """
    Parses currency / decimal values:
    - Optionally prefixed by ₹, Rs, Rs. or INR (case-insensitive, optional space).
    - Commas are thousands separators.
    - Must be >= 0.
    - Round to 2 places.
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None

    # Strip currency prefix
    cleaned = re.sub(r"^(?:₹|rs\.?|inr)\s*", "", s, flags=re.IGNORECASE).strip()
    # Remove thousands separators
    cleaned = cleaned.replace(",", "")

    try:
        num = float(cleaned)
        if num < 0:
            return None
        return round(num, 2)
    except ValueError:
        return None


def parse_enum(val: str | None, allowed_values: list[str]) -> str | None:
    """
    Validates enum against allowed values case-insensitively and returns lower-case.
    """
    if not val:
        return None
    s = str(val).strip().lower()
    allowed_map = {v.lower(): v.lower() for v in allowed_values}
    return allowed_map.get(s, None)


def parse_timestamp(val: str) -> datetime:
    """
    Parses an ISO event timestamp:
    - UTC: ends with 'Z'
    - Offset: e.g. +05:30
    - Naive: e.g. 2026-10-03 10:01:00 -> treated as IST per spec
    Returns timezone-aware IST datetime.
    """
    s = val.strip()
    if s.endswith("Z"):
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(IST)
    elif "T" in s and ("+" in s or "-" in s[10:]):
        dt = datetime.fromisoformat(s)
        return dt.astimezone(IST)
    else:
        # Naive datetime: represents IST per spec
        clean = s.replace("T", " ")
        dt = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=IST)
