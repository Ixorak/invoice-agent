"""The validation rules. Plain Python, no AI.

Each problem gets a code and a sentence a person can act on. The status is
derived from these codes only: OK when there are none, NEEDS_REVIEW otherwise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable, Sequence

from .iban import format_iban, iban_problem
from .injection import KeywordHit
from .models import InvoiceExtraction
from .parsing import normalize_iban
from .textutil import normalize_vat_id

OK = "OK"
NEEDS_REVIEW = "NEEDS_REVIEW"
EXTRACTION_FAILED = "EXTRACTION_FAILED"

# Codes in the order they are reported.
CODES = (
    "LINES_MISMATCH",
    "VAT_MISMATCH",
    "TOTAL_MISMATCH",
    "VAT_RATE_INVALID",
    "DUPLICATE",
    "MISSING_DUE_DATE",
    "MISSING_FIELD",
    "DUE_BEFORE_DATE",
    "IBAN_INVALID",
    "SUSPICIOUS_INSTRUCTION",
)

CODE_TITLES = {
    "LINES_MISMATCH": "Line items do not add up to the subtotal",
    "VAT_MISMATCH": "VAT amount does not match base x rate",
    "TOTAL_MISMATCH": "Subtotal + VAT does not match the printed total",
    "VAT_RATE_INVALID": "VAT rate is not a current Swiss rate",
    "DUPLICATE": "Same supplier and invoice number already recorded",
    "MISSING_DUE_DATE": "No due date",
    "MISSING_FIELD": "A required field is missing",
    "DUE_BEFORE_DATE": "Due date is before the invoice date",
    "IBAN_INVALID": "IBAN fails the mod-97 check",
    "SUSPICIOUS_INSTRUCTION": "Text that tries to instruct an automated system",
    "EXTRACTION_FAILED": "The AI could not return valid data",
}

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class Issue:
    code: str
    message: str


@dataclass(frozen=True)
class PreviousInvoice:
    """What the DUPLICATE rule needs to know about an invoice seen before."""

    file: str
    processed_at: str
    status: str


@dataclass
class CheckResult:
    issues: list[Issue]
    lines_sum: Decimal | None = None
    vat_used: Decimal | None = None
    computed_total: Decimal | None = None
    swiss: bool = False
    vat_table: list[dict] = field(default_factory=list)

    @property
    def codes(self) -> list[str]:
        return [issue.code for issue in self.issues]

    @property
    def status(self) -> str:
        return OK if not self.issues else NEEDS_REVIEW


def money(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{value.quantize(_CENT, rounding=ROUND_HALF_UP):,.2f}"
    except InvalidOperation:  # absurdly large number
        return str(value)


def rate_text(rate: Decimal | None) -> str:
    if rate is None:
        return "n/a"
    return f"{rate.normalize():f} %"


def _close(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    return abs(a - b) <= tolerance


def _same_rate(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) < Decimal("0.001")


def is_swiss_invoice(ex: InvoiceExtraction) -> bool:
    """Swiss VAT rules apply when the supplier's VAT number is a Swiss UID (CHE-...).

    Without a VAT number, a Swiss or Liechtenstein IBAN, or the CHF currency, is used instead.
    A foreign VAT number (e.g. IE..., DE...) means the invoice is not Swiss.
    """
    vat_key = normalize_vat_id(ex.supplier.vat_id)
    if vat_key:
        if vat_key.startswith("CHE"):  # a Swiss UID, even a slightly damaged one
            return True
        if re.match(r"[A-Z]{2}", vat_key):
            return False
    iban = normalize_iban(ex.payment.iban) or ""
    return iban[:2] in ("CH", "LI") or ex.invoice.currency == "CHF"


def is_text_only(line) -> bool:
    """An information line ("Delivery note 4711 of 01.03.") with no quantity, price or amount."""
    return line.quantity is None and line.unit_price is None and line.amount is None


def _vat_table(ex: InvoiceExtraction) -> list[dict]:
    """One row per VAT rate: rate, base, vat, plus how the base was obtained.

    Uses the VAT summary printed on the invoice. A missing base is taken from the
    lines with that rate, or from the subtotal when there is a single rate.
    Without a VAT summary, the lines are grouped by rate.
    """
    lines = [ln for ln in ex.line_items if not is_text_only(ln)]
    totals = ex.totals
    rows: list[dict] = []

    def lines_base(rate: Decimal) -> Decimal | None:
        matching = [ln.amount for ln in lines if ln.vat_rate is not None and _same_rate(ln.vat_rate, rate)]
        if matching and all(a is not None for a in matching):
            return sum(matching, Decimal(0))
        return None

    if totals.vat_breakdown:
        single = len(totals.vat_breakdown) == 1
        for entry in totals.vat_breakdown:
            base, source = entry.base, "printed"
            if base is None and entry.rate is not None:
                base, source = lines_base(entry.rate), "from lines"
                if base is None and single and totals.subtotal is not None:
                    base, source = totals.subtotal, "subtotal"
            vat = entry.vat
            if vat is None and single and totals.vat_total is not None:
                vat = totals.vat_total
            rows.append({"rate": entry.rate, "base": base, "vat": vat, "base_source": source})
        return rows

    rates: list[Decimal] = []
    for ln in lines:
        if ln.vat_rate is not None and not any(_same_rate(ln.vat_rate, r) for r in rates):
            rates.append(ln.vat_rate)
    for rate in rates:
        rows.append({"rate": rate, "base": lines_base(rate), "vat": None, "base_source": "from lines"})
    if len(rows) == 1 and totals.vat_total is not None:
        rows[0]["vat"] = totals.vat_total
    return rows


def run_checks(
    ex: InvoiceExtraction,
    *,
    tolerance: Decimal,
    swiss_rates: Sequence[Decimal],
    duplicate_of: PreviousInvoice | None = None,
    keyword_hits: Iterable[KeywordHit] = (),
) -> CheckResult:
    found: dict[str, list[str]] = {}

    def add(code: str, message: str) -> None:
        found.setdefault(code, []).append(message)

    totals = ex.totals
    tol = tolerance

    # LINES_MISMATCH: the line amounts must add up to the subtotal.
    # Information lines (no quantity, price or amount) are ignored. No priced line at all, or a
    # priced line without amount, is flagged even without a printed subtotal: otherwise nothing
    # would verify the total.
    priced = [(i, ln) for i, ln in enumerate(ex.line_items, 1) if not is_text_only(ln)]
    missing_amounts = [i for i, ln in priced if ln.amount is None]
    lines_sum: Decimal | None = None
    if priced and not missing_amounts:
        lines_sum = sum((ln.amount for _, ln in priced), Decimal(0))
    if not priced:
        add("LINES_MISMATCH", "No line item with an amount was found, so the amounts cannot be checked against them.")
    elif missing_amounts:
        add("LINES_MISMATCH", f"Line(s) {', '.join(map(str, missing_amounts))} have no amount, so the lines cannot be added up.")
    elif totals.subtotal is not None and not _close(lines_sum, totals.subtotal, tol):
        add("LINES_MISMATCH",
            f"The {len(priced)} line items add up to {money(lines_sum)}, but the printed subtotal is "
            f"{money(totals.subtotal)} (difference {money(lines_sum - totals.subtotal)}).")

    # VAT_MISMATCH: VAT of each rate = base x rate; the parts must add up to the VAT total.
    table = _vat_table(ex)
    for row in table:
        rate, base, vat = row["rate"], row["base"], row["vat"]
        if rate is None or base is None or vat is None:
            continue
        expected = base * rate / Decimal(100)
        row["expected_vat"] = expected
        if not _close(expected, vat, tol):
            add("VAT_MISMATCH",
                f"VAT at {rate_text(rate)} on {money(base)} should be {money(expected)}, but the invoice shows "
                f"{money(vat)} (difference {money(vat - expected)}).")
    # VAT that is charged but cannot be checked (rate or base unknown) is flagged too.
    for row in table:
        if row["vat"] is not None and abs(row["vat"]) > tol and (row["rate"] is None or row["base"] is None):
            missing_part = "rate" if row["rate"] is None else "taxable amount"
            add("VAT_MISMATCH", f"VAT of {money(row['vat'])} is charged but its {missing_part} is not known, "
                                "so it cannot be checked.")
    if not table and totals.vat_total is not None and abs(totals.vat_total) > tol:
        add("VAT_MISMATCH", f"A VAT total of {money(totals.vat_total)} is charged but no VAT rate was found, "
                            "so it cannot be checked.")
    printed_parts = [row["vat"] for row in table if row["vat"] is not None]
    if totals.vat_breakdown and totals.vat_total is not None and printed_parts and len(printed_parts) == len(table):
        parts_sum = sum(printed_parts, Decimal(0))
        if not _close(parts_sum, totals.vat_total, tol):
            add("VAT_MISMATCH",
                f"The VAT amounts per rate add up to {money(parts_sum)}, but the VAT total is {money(totals.vat_total)}.")
    if not totals.vat_breakdown and len(table) > 1 and totals.vat_total is not None:
        bases_known = [row for row in table if row["base"] is not None and row["rate"] is not None]
        if len(bases_known) == len(table):
            expected_total = sum((row["base"] * row["rate"] / Decimal(100) for row in table), Decimal(0))
            if not _close(expected_total, totals.vat_total, tol):
                add("VAT_MISMATCH",
                    f"VAT computed from the lines is {money(expected_total)}, but the VAT total is {money(totals.vat_total)}.")

    # TOTAL_MISMATCH: subtotal + VAT = printed total.
    subtotal = totals.subtotal if totals.subtotal is not None else lines_sum
    if totals.vat_total is not None:
        vat_used = totals.vat_total
    elif printed_parts:
        vat_used = sum(printed_parts, Decimal(0))
    else:
        vat_used = Decimal(0)
    computed_total = subtotal + vat_used if subtotal is not None else None
    if totals.total_printed is not None and computed_total is not None:
        if not _close(computed_total, totals.total_printed, tol):
            add("TOTAL_MISMATCH",
                f"Subtotal {money(subtotal)} + VAT {money(vat_used)} = {money(computed_total)}, but the printed total is "
                f"{money(totals.total_printed)} (difference {money(totals.total_printed - computed_total)}).")

    # VAT_RATE_INVALID: Swiss invoices may only use the current Swiss rates.
    swiss = is_swiss_invoice(ex)
    if swiss:
        used: list[Decimal] = []
        for rate in [row["rate"] for row in table] + [ln.vat_rate for ln in ex.line_items]:
            if rate is not None and not any(_same_rate(rate, r) for r in used):
                used.append(rate)
        invalid = [r for r in used if not any(_same_rate(r, allowed) for allowed in swiss_rates)]
        if invalid:
            allowed_text = ", ".join(rate_text(r) for r in swiss_rates)
            add("VAT_RATE_INVALID",
                f"{', '.join(rate_text(r) for r in invalid)} is not a current Swiss VAT rate (allowed: {allowed_text}).")

    # DUPLICATE: same supplier + same invoice number already recorded.
    if duplicate_of is not None:
        when = duplicate_of.processed_at.replace("T", " ")[:16]
        add("DUPLICATE",
            f"Same supplier and invoice number as '{duplicate_of.file}', already recorded on "
            f"{when} (status {duplicate_of.status}). Check that it is not paid twice.")

    # MISSING_DUE_DATE
    if ex.invoice.due_date is None:
        terms = f" Payment terms printed: \"{ex.payment.terms}\"." if ex.payment.terms else " No payment terms are printed either."
        add("MISSING_DUE_DATE", "No due date is printed on the invoice." + terms)

    # MISSING_FIELD: supplier, number, date, total, IBAN.
    missing = [
        label for label, value in (
            ("supplier", ex.supplier.name),
            ("invoice number", ex.invoice.number),
            ("invoice date", ex.invoice.date),
            ("total", totals.total_printed),
            ("IBAN", ex.payment.iban),
        ) if value is None
    ]
    if missing:
        add("MISSING_FIELD", "Missing: " + ", ".join(missing) + ".")

    # DUE_BEFORE_DATE
    if ex.invoice.date and ex.invoice.due_date and ex.invoice.due_date < ex.invoice.date:
        add("DUE_BEFORE_DATE",
            f"The due date {ex.invoice.due_date.isoformat()} is before the invoice date {ex.invoice.date.isoformat()}.")

    # IBAN_INVALID: ISO 13616 mod-97, computed here.
    if ex.payment.iban:
        problem = iban_problem(ex.payment.iban)
        if problem:
            add("IBAN_INVALID", f"The IBAN {format_iban(ex.payment.iban)} {problem}.")

    # SUSPICIOUS_INSTRUCTION: reported by the AI, or found by the keyword scan.
    hits = list(keyword_hits)
    if ex.suspicious_instructions or hits:
        parts = []
        if ex.suspicious_instructions:
            quoted = " | ".join(f"\"{s}\"" for s in ex.suspicious_instructions)
            parts.append(f"The AI reported: {quoted}.")
        if hits:
            rules = ", ".join(h.rule for h in hits)
            parts.append(f"The keyword scan matched {len(hits)} rule(s) ({rules}).")
            if not ex.suspicious_instructions:
                parts.append(f"Excerpt: \"{hits[0].excerpt}\".")
        add("SUSPICIOUS_INSTRUCTION",
            "The invoice contains text addressed to an automated system. It was not followed. " + " ".join(parts))

    issues = [Issue(code, " ".join(found[code])) for code in CODES if code in found]
    return CheckResult(
        issues=issues,
        lines_sum=lines_sum,
        vat_used=vat_used,
        computed_total=computed_total,
        swiss=swiss,
        vat_table=table,
    )
