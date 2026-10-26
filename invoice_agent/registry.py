"""Memory of every invoice already processed, kept in output/registry.json.

Two different questions are answered from it:

* "Was this exact file already processed?"  -> same SHA-256: the file is skipped.
* "Is this a DUPLICATE?"                    -> another file, same supplier and
  same invoice number: the invoice is processed and flagged for review.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .checks import PreviousInvoice
from .textutil import normalize_invoice_number, normalize_supplier_name, normalize_vat_id


class RegistryError(Exception):
    """output/registry.json exists but cannot be read."""


@dataclass
class Record:
    sha256: str
    file: str
    processed_at: str
    status: str
    supplier_name: str | None = None
    invoice_number: str | None = None
    vat_key: str | None = None
    name_key: str | None = None
    number_key: str | None = None
    json_report: str | None = None

    def as_previous(self) -> PreviousInvoice:
        return PreviousInvoice(file=self.file, processed_at=self.processed_at, status=self.status)


def supplier_keys(supplier_name: str | None, vat_id: str | None) -> tuple[str | None, str | None]:
    return normalize_vat_id(vat_id), normalize_supplier_name(supplier_name)


def same_supplier(vat_a: str | None, name_a: str | None, vat_b: str | None, name_b: str | None) -> bool:
    """Compare VAT numbers when both are known; otherwise compare normalized names."""
    if vat_a and vat_b:
        return vat_a == vat_b
    return bool(name_a and name_b and name_a == name_b)


class Registry:
    def __init__(self, path: Path, records: list[Record] | None = None):
        self.path = Path(path)
        self.records: list[Record] = records or []

    @classmethod
    def load(cls, path: Path) -> "Registry":
        path = Path(path)
        if not path.exists():
            return cls(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            fields = Record.__dataclass_fields__
            records = [Record(**{k: v for k, v in item.items() if k in fields}) for item in data["invoices"]]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RegistryError(
                f"{path} cannot be read ({exc}). It lists the invoices already processed; "
                "restore it from a backup or delete it to start from scratch."
            ) from exc
        return cls(path, records)

    def find_by_hash(self, sha256: str) -> Record | None:
        return next((r for r in self.records if r.sha256 == sha256), None)

    def find_duplicate(self, supplier_name: str | None, vat_id: str | None, invoice_number: str | None) -> Record | None:
        number_key = normalize_invoice_number(invoice_number)
        if not number_key:
            return None
        vat_key, name_key = supplier_keys(supplier_name, vat_id)
        for record in self.records:
            if record.number_key == number_key and same_supplier(vat_key, name_key, record.vat_key, record.name_key):
                return record
        return None

    def add(self, *, sha256: str, file: str, processed_at: str, status: str, supplier_name: str | None,
            vat_id: str | None, invoice_number: str | None, json_report: str | None) -> Record:
        vat_key, name_key = supplier_keys(supplier_name, vat_id)
        record = Record(
            sha256=sha256, file=file, processed_at=processed_at, status=status,
            supplier_name=supplier_name, invoice_number=invoice_number,
            vat_key=vat_key, name_key=name_key, number_key=normalize_invoice_number(invoice_number),
            json_report=json_report,
        )
        self.records.append(record)
        return record

    def save(self) -> None:
        """Write atomically: a crash never leaves a half-written registry."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "invoices": [asdict(r) for r in self.records]}
        fd, tmp = tempfile.mkstemp(prefix=".registry-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
