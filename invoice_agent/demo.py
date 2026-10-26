"""`demo`: a clean demo workspace, the 10 fictional sample invoices, and an evaluation."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .config import PROJECT_ROOT
from .evaluation import EvalReport

MARKER = ".invoice-agent-demo"
SAMPLES_DIR = PROJECT_ROOT / "samples"


class DemoFolderError(Exception):
    pass


def reset_demo_folder(root: Path) -> None:
    """Delete and recreate the demo folder.

    Only a folder created by a previous demo (it contains the marker file) or an
    empty folder is ever deleted.
    """
    root = root.resolve()
    forbidden = {Path(root.anchor), Path.home().resolve(), PROJECT_ROOT, SAMPLES_DIR.resolve()}
    if root in forbidden or root in SAMPLES_DIR.resolve().parents or SAMPLES_DIR.resolve().is_relative_to(root):
        raise DemoFolderError(f"Refusing to use {root} as the demo folder.")
    if root.exists():
        if not root.is_dir():
            raise DemoFolderError(f"{root} exists and is not a folder.")
        if any(root.iterdir()) and not (root / MARKER).exists():
            raise DemoFolderError(
                f"{root} is not empty and was not created by invoice-agent demo; it was not deleted. "
                "Choose another demo_folder in config.json."
            )
        try:
            shutil.rmtree(root)
        except OSError as exc:
            raise DemoFolderError(
                f"Cannot reset {root} (is a file open, e.g. invoices.xlsx in Excel, or is another demo running?): {exc}"
            ) from exc
    root.mkdir(parents=True)
    (root / MARKER).write_text("Created by `python -m invoice_agent demo`. Safe to delete.\n", encoding="utf-8")


def copy_samples(inbox: Path) -> list[Path]:
    inbox.mkdir(parents=True, exist_ok=True)
    copied = []
    for pdf in sorted((SAMPLES_DIR / "invoices").glob("*.pdf")):
        target = inbox / pdf.name
        shutil.copyfile(pdf, target)
        copied.append(target)
    return copied


def _mark(ok: bool) -> Text:
    return Text("yes", style="bold green") if ok else Text("NO", style="bold red")


def eval_tables(report: EvalReport) -> list[Table]:
    per_invoice = Table(title="Evaluation: status and codes", header_style="bold", title_style="bold")
    per_invoice.add_column("File", no_wrap=True)
    per_invoice.add_column("Expected")
    per_invoice.add_column("Got")
    per_invoice.add_column("Match", justify="center")
    per_invoice.add_column("Fields", justify="right")
    for inv in report.invoices:
        expected = inv.expected_status + (f" {', '.join(inv.expected_codes)}" if inv.expected_codes else "")
        got = inv.got_status + (f" {', '.join(inv.got_codes)}" if inv.got_codes else "")
        n_ok = sum(inv.fields.values())
        per_invoice.add_row(inv.file, expected, got, _mark(inv.status_ok), f"{n_ok}/{len(inv.fields)}")

    fields = Table(title="Evaluation: field accuracy", header_style="bold", title_style="bold")
    fields.add_column("Field")
    fields.add_column("Correct", justify="right")
    for label, ok, total in report.field_accuracy():
        style = "green" if ok == total else "red"
        fields.add_row(label, Text(f"{ok}/{total}", style=style))
    return [per_invoice, fields]


def print_evaluation(console: Console, report: EvalReport) -> None:
    for table in eval_tables(report):
        console.print()
        console.print(table)
    t = report.timing()
    n = len(report.invoices)
    style = "bold green" if report.status_correct == n else "bold red"
    console.print()
    console.print(Text.assemble(("Status and codes correct: ", "bold"), (f"{report.status_correct}/{n}", style)))
    if t:
        console.print(Text(
            f"Time per invoice: average {t['average']:.1f} s, median {t['median']:.1f} s, "
            f"min {t['min']:.1f} s, max {t['max']:.1f} s (total {t['total']:.0f} s)", style="dim"))
    for inv in report.invoices:
        for label, (exp, got) in inv.mismatches.items():
            console.print(Text(f"  {inv.file}: {label}: expected {exp!r}, got {got!r}", style="yellow"))


def markdown_report(report: EvalReport, *, backend_info: dict, models: list[str], started: datetime,
                    command: str = "python -m invoice_agent demo") -> str:
    n = len(report.invoices)
    t = report.timing()
    n_text = sum(1 for i in report.invoices if i.read_mode == "text")
    n_scan = sum(1 for i in report.invoices if i.read_mode == "scan")
    lines = [
        f"# Evaluation on the {n} fictional sample invoices",
        "",
        "This file is written by `python -m invoice_agent demo`. The numbers below are measured, not estimated.",
        "All sample invoices are **fictional** (invented companies, numbers and IBANs). "
        "A result on 10 synthetic invoices says nothing about accuracy on real invoices.",
        "",
        f"- Date: {started.strftime('%Y-%m-%d %H:%M')} (local time)",
        f"- Command: `{command}`",
        f"- Claude Code version: {backend_info.get('claude_code_version', 'unknown')}",
        f"- Model setting: `{backend_info.get('model_setting') or 'Claude Code default'}`"
        + (f", model reported by Claude Code: `{', '.join(models)}`" if models else ""),
        f"- Effort setting: `{backend_info.get('effort') or 'Claude Code default'}`",
        f"- Invoices: {n} ({n_text} read from their text layer, {n_scan} scanned and read by the AI from the PDF)",
        "",
        f"## Status and codes: {report.status_correct}/{n} correct",
        "",
        "| File | Expected | Got | Match | Time (s) |",
        "|---|---|---|---|---|",
    ]
    for inv in report.invoices:
        exp = inv.expected_status + (f" ({', '.join(inv.expected_codes)})" if inv.expected_codes else "")
        got = inv.got_status + (f" ({', '.join(inv.got_codes)})" if inv.got_codes else "")
        secs = f"{inv.seconds:.1f}" if inv.seconds is not None else "-"
        lines.append(f"| {inv.file} | {exp} | {got} | {'yes' if inv.status_ok else '**NO**'} | {secs} |")
    lines += [
        "",
        "## Field accuracy",
        "",
        "| Field | Correct |",
        "|---|---|",
    ]
    for label, ok, total in report.field_accuracy():
        lines.append(f"| {label} | {ok}/{total} |")
    lines += [
        "",
        "How fields are compared: names and invoice numbers ignoring case and extra spaces; VAT IDs and IBANs "
        "after removing spaces and punctuation; amounts to the cent; dates exactly. "
        "\"VAT breakdown\" and \"VAT total\" are the values used by the checks: a VAT base that is not printed "
        "is taken from the lines with that rate, and when no VAT total is printed the per-rate VAT amounts "
        "are added up.",
        "",
    ]
    mismatches = [(inv.file, label, exp, got) for inv in report.invoices for label, (exp, got) in inv.mismatches.items()]
    if mismatches:
        lines += ["Differences:", ""]
        lines += [f"- {f}: {label}: expected `{exp!r}`, got `{got!r}`" for f, label, exp, got in mismatches]
        lines.append("")
    if t:
        lines += [
            "## Time per invoice",
            "",
            f"- Average: {t['average']:.1f} s",
            f"- Median: {t['median']:.1f} s",
            f"- Min / max: {t['min']:.1f} s / {t['max']:.1f} s",
            f"- Total for {t['count']} invoices: {t['total']:.0f} s",
            "",
            "Measured from opening the PDF to the final status, including the Claude Code call "
            "(start-up of the CLI and the model's answer). Invoices are processed one after the other.",
            "",
        ]
    return "\n".join(lines)
