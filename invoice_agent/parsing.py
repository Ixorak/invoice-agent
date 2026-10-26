"""Deterministic parsing of the values the AI copies from an invoice.

The AI is asked to copy amounts exactly as printed ("1'245.80", "1 245,80",
"1,245.80", ...). Turning those strings into numbers is done here, in plain
Python, so it is predictable and covered by tests.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

# Characters used as thousands separators that can never be a decimal mark.
APOSTROPHES = "'’ʼ´`‘′"
SPACES = "         \t"

_CURRENCY_TOKENS = re.compile(
    r"(?i)(?<![a-z])(?:chf|eur|usd|gbp|sfr\.?|frs?\.)(?![a-z])|[€$£]"
)
_NULL_WORDS = {"", "null", "none", "n/a", "na", "nil", "-", "–", "—"}
_NUMBER_BODY = re.compile(r"[\d.,]*\d[\d.,]*")


class AmountError(ValueError):
    """Raised when a string cannot be read as an amount."""


class DateError(ValueError):
    """Raised when a string cannot be read as a date."""


def _strip_grouping(text: str) -> str:
    for ch in APOSTROPHES + SPACES:
        text = text.replace(ch, "")
    return text


_GROUPING_SPLIT = re.compile("[" + re.escape(APOSTROPHES + SPACES) + "]+")


def _check_grouping_chars(s: str, original: object) -> None:
    """With apostrophes or spaces as thousands separators, groups must have 3 digits: 1'245.80, not 1'24.80."""
    pieces = _GROUPING_SPLIT.split(s)
    if len(pieces) == 1:
        return
    first, *middle, last = pieces
    last_integer = re.split(r"[.,]", last, maxsplit=1)[0]
    if (
        not (first.isdigit() and 1 <= len(first) <= 3)
        or any(not (p.isdigit() and len(p) == 3) for p in middle)
        or not (last_integer.isdigit() and len(last_integer) == 3)
    ):
        raise AmountError(f"not an amount (bad digit grouping): {original!r}")


def _check_groups(integer_part: str, sep: str, original: object) -> None:
    groups = integer_part.split(sep)
    if not (1 <= len(groups[0]) <= 3) or any(len(g) != 3 for g in groups[1:]):
        raise AmountError(f"not an amount (bad digit grouping): {original!r}")


def _normalize_separators(s: str, decimal_separator: str | None, original: object) -> str:
    dots, commas = s.count("."), s.count(",")
    if dots and commas:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        if s.count(dec) != 1:
            raise AmountError(f"not an amount: {original!r}")
        integer_part, fraction = s.split(dec)
        _check_groups(integer_part, thou, original)
        return integer_part.replace(thou, "") + "." + fraction

    sep = "." if dots else ("," if commas else None)
    if sep is None:
        return s
    if s.count(sep) > 1:
        # "1.245.800" or "1,245,800": the separator can only be a thousands separator.
        _check_groups(s, sep, original)
        return s.replace(sep, "")

    integer_part, fraction = s.split(sep)
    if len(fraction) == 3 and integer_part not in ("", "0") and len(integer_part) <= 3:
        # "1,245" or "1.245" is ambiguous. Use the convention seen elsewhere on the
        # same invoice when we know it; otherwise read it as a thousands separator.
        if decimal_separator != sep:
            return integer_part + fraction
    return (integer_part or "0") + "." + fraction


def parse_amount(value: object, decimal_separator: str | None = None) -> Decimal | None:
    """Read an amount written in any common European or English format.

    >>> parse_amount("1'245.80"), parse_amount("1 245,80"), parse_amount("1,245.80")
    (Decimal('1245.80'), Decimal('1245.80'), Decimal('1245.80'))

    ``decimal_separator`` ("." or ",") is only used for ambiguous values such as
    "1,245" (one separator followed by exactly three digits).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise AmountError("a boolean is not an amount")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise AmountError(f"not an amount: {value!r}")
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AmountError(f"not an amount: {value!r}")
        return Decimal(repr(value))
    if not isinstance(value, str):
        raise AmountError(f"not an amount: {value!r}")

    s = value.strip()
    if s.lower() in _NULL_WORDS:
        return None
    s = _CURRENCY_TOKENS.sub(" ", s).strip()
    s = re.sub(r"[.,][-–—]{1,2}$", "", s)  # Swiss style "120.-" / "120.–"

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    if s[:1] in ("-", "−", "–"):
        negative, s = True, s[1:].strip()
    elif len(s) > 1 and s[-1:] in ("-", "−"):
        negative, s = True, s[:-1].strip()
    elif s[:1] == "+":
        s = s[1:].strip()

    _check_grouping_chars(s, value)
    s = _strip_grouping(s)
    if s[-1:] in (".", ","):
        s = s[:-1]
    if not _NUMBER_BODY.fullmatch(s):
        raise AmountError(f"not an amount: {value!r}")

    normalized = _normalize_separators(s, decimal_separator, value)
    try:
        result = Decimal(normalized)
    except InvalidOperation as exc:  # pragma: no cover - guarded by the regex above
        raise AmountError(f"not an amount: {value!r}") from exc
    return -result if negative else result


def parse_rate(value: object, decimal_separator: str | None = None) -> Decimal | None:
    """Read a VAT rate in percent: "8.1 %", "8,1", 8.1 -> Decimal("8.1")."""
    if isinstance(value, str):
        value = value.replace("%", " ").strip()
    rate = parse_amount(value, decimal_separator)
    if rate is not None and not (Decimal(0) <= rate < Decimal(100)):
        raise AmountError(f"not a VAT rate in percent: {value!r}")
    return rate


def parse_quantity(value: object, decimal_separator: str | None = None) -> Decimal | None:
    """Read a quantity and ignore a unit written next to it ("2.5 h", "40 kg", "3x")."""
    if isinstance(value, str):
        value = re.sub(r"[^\W\d_]+\.?", " ", value)  # unit words, with an optional dot ("Stk.")
        value = re.sub(r"\s+", " ", value).strip()
    return parse_amount(value, decimal_separator)


def infer_decimal_separator(values: Iterable[object]) -> str | None:
    """Guess which decimal mark an invoice uses from its unambiguous amounts.

    "486.00" or "1'094.00" vote for "."; "1 245,80" votes for ",".
    Returns None when there is no evidence or the evidence is split evenly.
    """
    votes = {".": 0, ",": 0}
    for value in values:
        if not isinstance(value, str):
            continue
        s = _strip_grouping(_CURRENCY_TOKENS.sub("", value.replace("%", "")))
        has_dot, has_comma = "." in s, "," in s
        if has_dot and has_comma:
            votes["." if s.rfind(".") > s.rfind(",") else ","] += 1
        elif has_dot or has_comma:
            sep = "." if has_dot else ","
            if s.count(sep) == 1:
                fraction = s.rsplit(sep, 1)[1]
                if fraction.isdigit() and len(fraction) in (1, 2):
                    votes[sep] += 1
    if votes["."] == votes[","]:
        return None
    return "." if votes["."] > votes[","] else ","


def parse_date(value: object) -> date | None:
    """Read a date. ISO (YYYY-MM-DD) first, then day-first numeric dates (DD.MM.YYYY)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise DateError(f"not a date: {value!r}")
    s = value.strip()
    if s.lower() in _NULL_WORDS:
        return None
    try:
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?", s)
        if m:
            return date(int(m[1]), int(m[2]), int(m[3]))
        m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})", s)
        if m:
            year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
            return date(year, int(m[2]), int(m[1]))
    except ValueError as exc:
        raise DateError(f"not a valid date: {value!r}") from exc
    raise DateError(f"not a date (expected YYYY-MM-DD): {value!r}")


_CURRENCY_ALIASES = {
    "€": "EUR", "EURO": "EUR", "EUROS": "EUR",
    "FR": "CHF", "FR.": "CHF", "FRS": "CHF", "FRS.": "CHF", "SFR": "CHF", "SFR.": "CHF",
    "$": "USD", "US$": "USD", "£": "GBP",
}


def parse_currency(value: object) -> str | None:
    """Normalize a currency to its ISO 4217 code ("chf" -> "CHF", "€" -> "EUR")."""
    if value is None:
        return None
    s = str(value).strip().upper()
    if s.lower() in _NULL_WORDS:
        return None
    s = _CURRENCY_ALIASES.get(s, s)
    if not re.fullmatch(r"[A-Z]{3}", s):
        raise ValueError(f"currency must be an ISO 4217 code such as CHF or EUR, got {value!r}")
    return s


def parse_language(value: object) -> str | None:
    """Normalize a language to its ISO 639-1 code ("FR", "fr-CH" -> "fr")."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in _NULL_WORDS:
        return None
    m = re.fullmatch(r"([a-z]{2})(?:[-_][a-z]{2,4})?", s)
    if not m:
        raise ValueError(f"language must be an ISO 639-1 code such as fr or de, got {value!r}")
    return m[1]


def normalize_iban(value: object) -> str | None:
    """Uppercase an IBAN and remove spaces, dots, dashes and a leading "IBAN" label."""
    if value is None:
        return None
    s = str(value).upper()
    s = re.sub(r"^\s*IBAN\b[\s:.]*", "", s)
    s = re.sub(r"[\s.\-  ]", "", s)
    return s or None
