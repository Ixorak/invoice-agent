"""Small text helpers: accent folding and the keys used to detect duplicates."""

from __future__ import annotations

import re
import unicodedata

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f​-‏‪-‮⁠-⁤﻿]")


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def clean_text(value: object) -> str | None:
    """Trim a string, drop control / zero-width characters, turn "" into None."""
    if value is None:
        return None
    text = _CONTROL.sub("", str(value)).strip()
    return text or None


# A Swiss UID: CHE-123.456.789 (often followed by MWST / TVA / IVA). Any separator is
# accepted between the groups: spaces, dots, and every kind of dash (Word often writes
# a non-breaking hyphen or an en dash instead of "-").
_SWISS_UID = re.compile(r"CHE[^A-Z0-9]*(\d{3})[^A-Z0-9]*(\d{3})[^A-Z0-9]*(\d{3})(?!\d)")
_VAT_LABEL = re.compile(
    r"^\s*(?:VAT|UST|MWST|TVA|IVA|UID|IDE|IDI)\b[^A-Z0-9]*"
    r"(?:(?:REG(?:ISTRATION)?|NO|NR|NUMBER|ID|IDNR)\b[^A-Z0-9]*)?"
)


def normalize_vat_id(value: str | None) -> str | None:
    """Comparable form of a VAT / UID number.

    "CHE-482.915.306 TVA" -> "CHE482915306", "IE 9999999XA (note)" -> "IE9999999XA".
    """
    if not value:
        return None
    text = strip_accents(value).upper()
    m = _SWISS_UID.search(text)
    if m:
        return "CHE" + "".join(m.groups())
    text = re.sub(r"\([^)]*\)", " ", text)
    text = _VAT_LABEL.sub("", text)
    text = re.sub(r"[^A-Z0-9]", "", text)
    return text or None


def is_swiss_uid(value: str | None) -> bool:
    key = normalize_vat_id(value)
    return bool(key and re.fullmatch(r"CHE\d{9}", key))


def is_reliable_vat_key(key: str | None) -> bool:
    """False for a Swiss number that is visibly damaged (e.g. "CHE12894653O" from a bad scan)."""
    if not key:
        return False
    return not key.startswith("CHE") or bool(re.fullmatch(r"CHE\d{9}", key))


_LEGAL_FORMS = {
    "sa", "sarl", "sagl", "gmbh", "ag", "kg", "ltd", "limited", "llc", "inc", "corp",
    "srl", "spa", "sas", "sasu", "eurl", "bv", "nv", "plc", "oy", "ab", "co", "cie",
    "rl", "gesmbh", "ug", "eg", "se",
}


def normalize_supplier_name(value: str | None) -> str | None:
    """Comparable form of a company name: lowercase, no accents, no legal form.

    "Papeterie Lumenta Sàrl" -> "papeterie lumenta".
    """
    if not value:
        return None
    text = strip_accents(value).lower().replace(".", "")
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = text.split()
    while len(tokens) > 1 and tokens[-1] in _LEGAL_FORMS:
        tokens.pop()
    return " ".join(tokens) or None


def normalize_invoice_number(value: str | None) -> str | None:
    """Comparable form of an invoice number: "F-2026-0418" -> "F20260418"."""
    if not value:
        return None
    text = re.sub(r"[^A-Z0-9]", "", strip_accents(value).upper())
    return text or None
