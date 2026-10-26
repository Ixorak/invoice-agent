"""Compare the agent's results with samples/expected.json (ground truth written by hand)."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .parsing import normalize_iban
from .pipeline import InvoiceResult
from .textutil import normalize_vat_id

CENT = Decimal("0.005")

# (label, how the value is compared)
FIELDS = [
    ("Supplier name", "text"),
    ("Supplier VAT ID", "vat"),
    ("Invoice number", "text"),
    ("Invoice date", "exact"),
    ("Due date", "exact"),
    ("Currency", "exact"),
    ("Subtotal", "money"),
    ("VAT breakdown", "vat_breakdown"),
    ("VAT total", "money"),
    ("Total (printed)", "money"),
    ("IBAN", "iban"),
    ("Language", "exact"),
    ("Line count", "exact"),
    ("Scanned (no text layer)", "exact"),
]


@dataclass
class InvoiceEval:
    file: str
    expected_status: str
    got_status: str
    expected_codes: list[str]
    got_codes: list[str]
    fields: dict[str, bool] = field(default_factory=dict)
    mismatches: dict[str, tuple] = field(default_factory=dict)
    seconds: float | None = None
    read_mode: str | None = None

    @property
    def status_ok(self) -> bool:
        return self.expected_status == self.got_status and sorted(self.expected_codes) == sorted(self.got_codes)


@dataclass
class EvalReport:
    invoices: list[InvoiceEval]

    @property
    def status_correct(self) -> int:
        return sum(1 for i in self.invoices if i.status_ok)

    def field_accuracy(self) -> list[tuple[str, int, int]]:
        rows = []
        for label, _ in FIELDS:
            values = [i.fields[label] for i in self.invoices if label in i.fields]
            rows.append((label, sum(values), len(values)))
        return rows

    def durations(self) -> list[float]:
        return [i.seconds for i in self.invoices if i.seconds is not None]

    def timing(self) -> dict:
        d = self.durations()
        if not d:
            return {}
        return {
            "count": len(d), "average": statistics.mean(d), "median": statistics.median(d),
            "min": min(d), "max": max(d), "total": sum(d),
        }


def _text(v):
    return " ".join(str(v).split()).casefold() if v is not None else None


def _same(kind: str, expected, got) -> bool:
    if kind == "text":
        return _text(expected) == _text(got)
    if kind == "vat":
        return normalize_vat_id(expected) == normalize_vat_id(got)
    if kind == "iban":
        return normalize_iban(expected) == normalize_iban(got)
    if kind == "money":
        if expected is None or got is None:
            return expected is None and got is None
        return abs(Decimal(str(expected)) - Decimal(str(got))) <= CENT
    if kind == "vat_breakdown":
        def key(rows):
            return sorted((Decimal(str(r["rate"])), Decimal(str(r["base"])) if r["base"] is not None else None,
                           Decimal(str(r["vat"])) if r["vat"] is not None else None) for r in rows)
        try:
            e, g = key(expected), key(got)
        except (KeyError, TypeError):
            return False
        if len(e) != len(g):
            return False
        for (er, eb, ev), (gr, gb, gv) in zip(e, g):
            if abs(er - gr) > Decimal("0.001"):
                return False
            for a, b in ((eb, gb), (ev, gv)):
                if (a is None) != (b is None) or (a is not None and abs(a - b) > CENT):
                    return False
        return True
    return expected == got


def _got_values(r: InvoiceResult) -> dict:
    ex = r.extraction
    scanned = r.read_mode == "scan" if r.read_mode else None
    if ex is None:
        return {"Scanned (no text layer)": scanned}
    table = r.checks.vat_table if r.checks else []
    return {
        "Supplier name": ex.supplier.name,
        "Supplier VAT ID": ex.supplier.vat_id,
        "Invoice number": ex.invoice.number,
        "Invoice date": ex.invoice.date.isoformat() if ex.invoice.date else None,
        "Due date": ex.invoice.due_date.isoformat() if ex.invoice.due_date else None,
        "Currency": ex.invoice.currency,
        "Subtotal": ex.totals.subtotal,
        "VAT breakdown": [{"rate": row["rate"], "base": row["base"], "vat": row["vat"]} for row in table],
        # The VAT total used by the checks: printed, or the sum of the per-rate VAT when no total is printed.
        "VAT total": r.checks.vat_used if r.checks else ex.totals.vat_total,
        "Total (printed)": ex.totals.total_printed,
        "IBAN": ex.payment.iban,
        "Language": ex.language,
        "Line count": len(ex.line_items),
        "Scanned (no text layer)": scanned,
    }


def _expected_values(e: dict) -> dict:
    return {
        "Supplier name": e.get("supplier_name"),
        "Supplier VAT ID": e.get("supplier_vat_id"),
        "Invoice number": e.get("invoice_number"),
        "Invoice date": e.get("invoice_date"),
        "Due date": e.get("due_date"),
        "Currency": e.get("currency"),
        "Subtotal": e.get("subtotal"),
        "VAT breakdown": e.get("vat_breakdown"),
        "VAT total": e.get("vat_total"),
        "Total (printed)": e.get("total_printed"),
        "IBAN": e.get("iban"),
        "Language": e.get("language"),
        "Line count": e.get("line_count"),
        "Scanned (no text layer)": e.get("scanned"),
    }


def evaluate(results: list[InvoiceResult], expected_path: Path) -> EvalReport:
    expected = json.loads(Path(expected_path).read_text(encoding="utf-8"))["invoices"]
    by_file = {r.file: r for r in results}
    rows: list[InvoiceEval] = []
    for exp in expected:
        r = by_file.get(exp["file"])
        row = InvoiceEval(
            file=exp["file"],
            expected_status=exp["expected_status"],
            got_status=r.status if r else "MISSING",
            expected_codes=list(exp["expected_flags"]),
            got_codes=r.codes if r else [],
            seconds=r.duration_seconds if r else None,
            read_mode=r.read_mode if r else None,
        )
        got = _got_values(r) if r else {}
        for label, kind in FIELDS:
            exp_value = _expected_values(exp)[label]
            ok = label in got and _same(kind, exp_value, got[label])
            row.fields[label] = ok
            if not ok:
                row.mismatches[label] = (exp_value, got.get(label))
        rows.append(row)
    return EvalReport(rows)
