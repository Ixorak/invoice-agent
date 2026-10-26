"""IBAN check digits (ISO 13616, mod 97), computed in Python."""

from __future__ import annotations

import re

from .parsing import normalize_iban

# 2 letters (country), 2 check digits, then 11 to 30 letters or digits (15 to 34 in total).
_IBAN_SHAPE = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}")


def mod97(iban: str) -> int:
    """Return the ISO 13616 remainder. A valid IBAN gives 1."""
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)  # A=10 ... Z=35
    return int(digits) % 97


def iban_problem(value: str | None) -> str | None:
    """Explain why an IBAN is invalid, or return None when it passes."""
    iban = normalize_iban(value)
    if not iban:
        return "no IBAN"
    if not _IBAN_SHAPE.fullmatch(iban):
        return "is not a well-formed IBAN (2 letters, 2 check digits, then 11 to 30 letters or digits)"
    if mod97(iban) != 1:
        return "fails the ISO 13616 mod-97 check (wrong check digits or a mistyped character)"
    return None


def is_valid_iban(value: str | None) -> bool:
    return iban_problem(value) is None


def format_iban(value: str | None) -> str | None:
    """Group an IBAN by 4 characters for display: CH93 0076 2011 ..."""
    iban = normalize_iban(value)
    if not iban:
        return None
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))
