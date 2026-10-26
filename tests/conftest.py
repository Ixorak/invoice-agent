"""Shared helpers. No test calls the AI: a fake backend returns prepared answers.

The invoice data used here is invented for the tests (it is not one of the
sample invoices) so that the rules are tested on their own.
"""

from __future__ import annotations

import copy
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from invoice_agent.backends import Backend, BackendReply
from invoice_agent.checks import run_checks
from invoice_agent.config import Config, Workspace
from invoice_agent.models import InvoiceExtraction

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples" / "invoices"
SWISS = [Decimal("8.1"), Decimal("2.6"), Decimal("3.8"), Decimal("0")]
VALID_CH_IBAN = "CH93 0076 2011 6238 5295 7"  # example IBAN published in the SWIFT IBAN registry


def base_payload() -> dict:
    """A correct Swiss invoice, as the AI would return it (values copied as printed)."""
    return {
        "supplier": {"name": "Muster Handel AG", "address": "Bahnhofstrasse 1, 8001 Zürich",
                     "vat_id": "CHE-123.456.789 MWST"},
        "invoice": {"number": "R-1001", "date": "2026-03-02", "due_date": "2026-04-01", "currency": "CHF"},
        "line_items": [
            {"description": "Beratung", "quantity": "2", "unit_price": "100.00", "vat_rate": "8.1", "amount": "200.00"},
            {"description": "Spesen", "quantity": "1", "unit_price": "50.00", "vat_rate": "8.1", "amount": "50.00"},
        ],
        "totals": {
            "subtotal": "250.00",
            "vat_breakdown": [{"rate": "8.1", "base": "250.00", "vat": "20.25"}],
            "vat_total": "20.25",
            "total_printed": "270.25",
        },
        "payment": {"iban": VALID_CH_IBAN, "terms": "30 Tage netto"},
        "language": "de",
        "suspicious_instructions": [],
    }


def payload(changes: dict | None = None) -> dict:
    """base_payload() with changes given as dotted paths: {"totals.total_printed": "280.25"}."""
    data = copy.deepcopy(base_payload())
    for dotted, value in (changes or {}).items():
        node = data
        *parents, last = dotted.split(".")
        for key in parents:
            node = node[int(key)] if isinstance(node, list) else node[key]
        if isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value
    return data


def extraction(changes: dict | None = None) -> InvoiceExtraction:
    return InvoiceExtraction.from_ai(payload(changes))


def codes(ex: InvoiceExtraction, tolerance: str = "0.05", rates=None, **kwargs) -> list[str]:
    result = run_checks(ex, tolerance=Decimal(tolerance), swiss_rates=rates or SWISS, **kwargs)
    return result.codes


class FakeBackend(Backend):
    """Returns the prepared answers in order (the last one is repeated)."""

    name = "fake"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def describe(self) -> dict:
        return {"backend": "fake", "claude_code_version": "test", "model_setting": None, "effort": None}

    def complete(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if callable(answer):
            answer = answer(request)
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, dict):
            return BackendReply(text=json.dumps(answer), data=answer, seconds=0.01, meta={"model": "fake-model"})
        return BackendReply(text=str(answer), data=None, seconds=0.01, meta={"model": "fake-model"})


@pytest.fixture
def workspace(tmp_path) -> Workspace:
    ws = Workspace.under(tmp_path / "ws")
    ws.create()
    return ws


@pytest.fixture
def config() -> Config:
    return Config()


def copy_sample(name: str, folder: Path, new_name: str | None = None) -> Path:
    target = folder / (new_name or name)
    shutil.copyfile(SAMPLES / name, target)
    return target


def make_pdf(text: str) -> bytes:
    """A minimal one-page PDF whose text layer is `text` (to test PDFs with a short text layer)."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
