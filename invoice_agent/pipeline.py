"""Processing of one invoice and of the whole inbox.

For each PDF: fingerprint -> read -> AI extraction -> Python checks ->
JSON report -> Excel -> registry -> move to processed/ (OK) or review/.

The agent only reads PDFs and writes inside its own folders. It never pays,
sends, contacts anyone or modifies a PDF.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from . import __version__
from .backends import Backend, BackendError
from .checks import CODE_TITLES, EXTRACTION_FAILED, OK, CheckResult, Issue, run_checks
from .config import Config, Workspace
from .excel import append_results, ensure_writable
from .extractor import Attempt, Extractor
from .injection import KeywordHit, scan_text
from .models import InvoiceExtraction
from .pdf_reader import PdfReadError, read_pdf, sha256_file
from .registry import Registry

SKIPPED = "SKIPPED"


@dataclass
class InvoiceResult:
    file: str
    sha256: str
    status: str
    processed_at: datetime
    issues: list[Issue] = field(default_factory=list)
    extraction: InvoiceExtraction | None = None
    checks: CheckResult | None = None
    read_mode: str | None = None          # "text" or "scan"
    pages: int | None = None
    keyword_hits: list[KeywordHit] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    duration_seconds: float = 0.0
    error: str | None = None
    note: str | None = None               # e.g. why a file was skipped
    moved_to: Path | None = None
    json_path: Path | None = None
    base_dir: Path | None = None

    @property
    def codes(self) -> list[str]:
        return [i.code for i in self.issues if i.code != EXTRACTION_FAILED]

    @property
    def ai_seconds(self) -> float:
        return sum(a.seconds for a in self.attempts)

    def _display(self, path: Path | None) -> str | None:
        if path is None:
            return None
        if self.base_dir is not None:
            try:
                return path.relative_to(self.base_dir).as_posix()
            except ValueError:
                pass
        return str(path)

    @property
    def moved_to_display(self) -> str | None:
        return self._display(self.moved_to)

    @property
    def json_display(self) -> str | None:
        return self._display(self.json_path)


@dataclass
class BatchResult:
    results: list[InvoiceResult]
    error: str | None = None  # set when the run stopped early (e.g. Claude Code failed)

    @property
    def processed(self) -> list[InvoiceResult]:
        return [r for r in self.results if r.status != SKIPPED]


class Reporter(Protocol):
    def started(self, path: Path, index: int, total: int, scanned: bool | None) -> None: ...
    def finished(self, result: InvoiceResult) -> None: ...


class _SilentReporter:
    def started(self, path, index, total, scanned):
        pass

    def finished(self, result):
        pass


def unique_path(folder: Path, name: str, sha256: str) -> Path:
    """folder/name, or folder/name-<hash>.ext if a different file already has that name."""
    candidate = folder / name
    if not candidate.exists():
        return candidate
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate = folder / f"{stem}-{sha256[:8]}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = folder / f"{stem}-{sha256[:8]}-{counter}{suffix}"
        counter += 1
    return candidate


def list_pdfs(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"),
                  key=lambda p: p.name.lower())


class Agent:
    def __init__(self, config: Config, workspace: Workspace, backend: Backend, reporter: Reporter | None = None):
        self.config = config
        self.workspace = workspace
        self.backend = backend
        self.reporter = reporter or _SilentReporter()
        self.extractor = Extractor(backend)
        workspace.create()
        self.registry = Registry.load(workspace.registry_path)
        self.backend_info = backend.describe()
        try:
            folders = [workspace.inbox, workspace.processed, workspace.review, workspace.output]
            self.base_dir: Path | None = Path(os.path.commonpath(folders))
        except ValueError:  # folders on different drives
            self.base_dir = None

    # ------------------------------------------------------------------ run
    def process_inbox(self, files: list[Path] | None = None) -> BatchResult:
        files = list_pdfs(self.workspace.inbox) if files is None else files
        results: list[InvoiceResult] = []
        for index, path in enumerate(files, start=1):
            try:
                result = self.process_file(path, index, len(files))
            except BackendError as exc:
                return BatchResult(results, f"{path.name}: {exc} The file was left in the inbox.")
            results.append(result)
        return BatchResult(results)

    def process_file(self, path: Path, index: int = 1, total: int = 1) -> InvoiceResult:
        started = time.perf_counter()
        processed_at = datetime.now().astimezone()
        sha = sha256_file(path)

        known = self.registry.find_by_hash(sha)
        if known is not None:
            result = InvoiceResult(
                file=path.name, sha256=sha, status=SKIPPED, processed_at=processed_at,
                note=f"already processed on {known.processed_at} as '{known.file}' ({known.status}); left in the inbox",
                base_dir=self.base_dir,
            )
            self.reporter.finished(result)
            return result

        ensure_writable(self.workspace.excel_path)  # before spending an AI call
        result = InvoiceResult(file=path.name, sha256=sha, status=EXTRACTION_FAILED, processed_at=processed_at,
                               base_dir=self.base_dir)
        try:
            content = read_pdf(path, self.config.min_text_chars, self.config.max_text_chars)
        except PdfReadError as exc:
            self.reporter.started(path, index, total, None)
            result.error = str(exc)
            result.issues = [Issue(EXTRACTION_FAILED, f"The file could not be read as a PDF: {exc}")]
        else:
            result.read_mode, result.pages = ("scan" if content.scanned else "text"), content.pages
            self.reporter.started(path, index, total, content.scanned)
            outcome = self.extractor.extract(content, path)  # BackendError -> caller stops the run
            result.attempts = outcome.attempts
            if outcome.extraction is None:
                result.error = outcome.error
                result.issues = [Issue(EXTRACTION_FAILED, outcome.error or "unknown error")]
            else:
                ex = outcome.extraction
                result.extraction = ex
                scan_source = content.text if not content.scanned else "\n".join(ex.text_fields())
                result.keyword_hits = scan_text(scan_source)
                previous = self.registry.find_duplicate(ex.supplier.name, ex.supplier.vat_id, ex.invoice.number)
                result.checks = run_checks(
                    ex,
                    tolerance=self.config.tolerance,
                    swiss_rates=self.config.swiss_vat_rates,
                    duplicate_of=previous.as_previous() if previous else None,
                    keyword_hits=result.keyword_hits,
                )
                result.issues = result.checks.issues
                result.status = result.checks.status
        result.duration_seconds = time.perf_counter() - started

        destination_folder = self.workspace.processed if result.status == OK else self.workspace.review
        destination = unique_path(destination_folder, path.name, sha)
        result.moved_to = destination
        result.json_path = unique_path(self.workspace.json_dir, path.stem + ".json", sha)

        self._write_json(result)
        append_results(self.workspace.excel_path, [result])
        ex = result.extraction
        self.registry.add(
            sha256=sha, file=path.name, processed_at=processed_at.isoformat(timespec="seconds"),
            status=result.status,
            supplier_name=ex.supplier.name if ex else None,
            vat_id=ex.supplier.vat_id if ex else None,
            invoice_number=ex.invoice.number if ex else None,
            json_report=result.json_display,
        )
        self.registry.save()
        try:
            shutil.move(str(path), str(destination))
        except OSError as exc:
            result.moved_to = None
            result.note = f"could not be moved out of the inbox: {exc}"
            self._write_json(result)
        self.reporter.finished(result)
        return result

    # --------------------------------------------------------------- output
    def report(self, r: InvoiceResult) -> dict:
        checks = r.checks
        return {
            "file": r.file,
            "sha256": r.sha256,
            "processed_at": r.processed_at.isoformat(timespec="seconds"),
            "status": r.status,
            "codes": r.codes,
            "issues": [{"code": i.code, "title": CODE_TITLES.get(i.code, i.code), "message": i.message} for i in r.issues],
            "read_from": r.read_mode,
            "pages": r.pages,
            "duration_seconds": round(r.duration_seconds, 2),
            "ai_seconds": round(r.ai_seconds, 2),
            "extraction": r.extraction.model_dump(mode="json") if r.extraction else None,
            "checks": None if checks is None else {
                "tolerance": float(self.config.tolerance),
                "swiss_invoice": checks.swiss,
                "lines_sum": _num(checks.lines_sum),
                "vat_used": _num(checks.vat_used),
                "computed_total": _num(checks.computed_total),
                "vat_table": [{k: (_num(v) if not isinstance(v, str) else v) for k, v in row.items()} for row in checks.vat_table],
                "keyword_hits": [{"rule": h.rule, "excerpt": h.excerpt} for h in r.keyword_hits],
            },
            "attempts": [
                {"attempt": a.number, "seconds": round(a.seconds, 2), "valid": a.ok, "error": a.error, **a.meta}
                for a in r.attempts
            ],
            "error": r.error,
            "note": r.note,
            "moved_to": r.moved_to_display,
            "backend": self.backend_info,
            "agent_version": __version__,
        }

    def _write_json(self, r: InvoiceResult) -> None:
        assert r.json_path is not None
        r.json_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = r.json_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.report(r), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, r.json_path)


def _num(value):
    return None if value is None else float(value)


__all__ = ["Agent", "BatchResult", "InvoiceResult", "SKIPPED", "list_pdfs", "unique_path"]
