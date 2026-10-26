"""Console output with rich. Text that comes from an invoice is never read as markup."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .checks import EXTRACTION_FAILED, NEEDS_REVIEW, OK, money
from .pipeline import SKIPPED, InvoiceResult

STATUS_STYLE = {OK: "bold green", NEEDS_REVIEW: "bold dark_orange", EXTRACTION_FAILED: "bold red", SKIPPED: "dim"}
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def make_console(record: bool = False, width: int | None = None) -> Console:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except (OSError, ValueError):  # pragma: no cover
            pass
    return Console(record=record, width=width, highlight=False)


def plain(value: object) -> Text:
    """Untrusted text: no rich markup, no terminal control characters."""
    if value is None:
        return Text("")
    return Text(_CONTROL.sub(" ", str(value)))


def status_text(status: str) -> Text:
    return Text(status, style=STATUS_STYLE.get(status, ""))


class ConsoleReporter:
    """Prints one line per invoice while the agent works."""

    def __init__(self, console: Console):
        self.console = console

    def started(self, path: Path, index: int, total: int, scanned: bool | None) -> None:
        mode = "scanned PDF, the AI reads the file" if scanned else ("text PDF" if scanned is False else "unreadable PDF")
        line = Text.assemble((f"[{index}/{total}] ", "dim"), plain(path.name), (f"  {mode} ...", "dim"))
        self.console.print(line)

    def finished(self, result: InvoiceResult) -> None:
        if result.status == SKIPPED:
            self.console.print(Text.assemble(("  skipped ", "dim"), plain(result.file), (f": {result.note}", "dim")))
            return
        codes = ", ".join(result.codes) or ("-" if result.status == OK else result.error or "")
        self.console.print(Text.assemble(
            "      -> ", status_text(result.status), "  ", plain(codes[:120]),
            (f"  ({result.duration_seconds:.1f} s)", "dim"),
        ))
        if result.note and result.status != SKIPPED:
            self.console.print(Text.assemble(("      note: ", "yellow"), plain(result.note)))


def summary_table(results: list[InvoiceResult], title: str = "invoice-agent: summary") -> Table:
    table = Table(title=title, title_style="bold", header_style="bold", box=box.ROUNDED, show_lines=True)
    table.add_column("File", no_wrap=True)
    table.add_column("Supplier", max_width=26)
    table.add_column("Invoice no.", no_wrap=True)
    table.add_column("Total", justify="right", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Codes", no_wrap=True)
    table.add_column("Time", justify="right", no_wrap=True)
    for r in results:
        ex = r.extraction
        total = ""
        if ex and ex.totals.total_printed is not None:
            total = f"{ex.invoice.currency or ''} {money(ex.totals.total_printed)}".strip()
        if r.status == SKIPPED:
            codes = Text("already processed (same SHA-256)", style="dim")
        elif r.status == EXTRACTION_FAILED:
            codes = plain((r.error or "").splitlines()[0][:50])
        else:
            codes = plain("\n".join(r.codes) if r.codes else "-")
        table.add_row(
            plain(r.file), plain(ex.supplier.name if ex else ""), plain(ex.invoice.number if ex else ""),
            total, status_text(r.status), codes,
            f"{r.duration_seconds:.1f} s" if r.status != SKIPPED else "",
        )
    return table


def totals_line(results: list[InvoiceResult]) -> Text:
    done = [r for r in results if r.status != SKIPPED]
    counts = {s: sum(1 for r in results if r.status == s) for s in (OK, NEEDS_REVIEW, EXTRACTION_FAILED, SKIPPED)}
    parts: list = [(f"{len(done)} processed", "bold"), ":  "]
    for status in (OK, NEEDS_REVIEW, EXTRACTION_FAILED):
        parts += [(f"{counts[status]} {status}", STATUS_STYLE[status]), "   "]
    if counts[SKIPPED]:
        parts += [(f"{counts[SKIPPED]} skipped", "dim"), "   "]
    if done:
        avg = sum(r.duration_seconds for r in done) / len(done)
        parts.append((f"average {avg:.1f} s per invoice", "dim"))
    return Text.assemble(*parts)


def export(console: Console, path: Path, title: str = "invoice-agent") -> None:
    """Save everything printed so far (svg, html or txt, from the file extension)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".svg":
        console.save_svg(str(path), title=title, clear=False)
    elif suffix in (".html", ".htm"):
        console.save_html(str(path), clear=False)
    else:
        console.save_text(str(path), clear=False)
