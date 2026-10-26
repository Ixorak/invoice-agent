"""The whole pipeline with a fake AI: folders, Excel, JSON, registry, skipping, duplicates."""

import json
from pathlib import Path

from openpyxl import load_workbook

from invoice_agent.backends import BackendError
from invoice_agent.pipeline import SKIPPED, Agent, list_pdfs
from invoice_agent.registry import Registry

from .conftest import FakeBackend, copy_sample, make_pdf, payload


def _agent(config, workspace, *answers):
    return Agent(config, workspace, FakeBackend(*answers))


def _rows(path: Path, sheet: str) -> list[tuple]:
    wb = load_workbook(path)
    return list(wb[sheet].iter_rows(min_row=2, values_only=True))


def test_ok_invoice_goes_to_processed_with_json_and_excel(config, workspace):
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "a.pdf")
    batch = _agent(config, workspace, payload()).process_inbox()
    assert batch.error is None
    [result] = batch.results
    assert result.status == "OK"
    assert (workspace.processed / "a.pdf").exists() and not (workspace.inbox / "a.pdf").exists()
    report = json.loads((workspace.json_dir / "a.json").read_text(encoding="utf-8"))
    assert report["status"] == "OK" and report["extraction"]["invoice"]["number"] == "R-1001"
    assert report["duration_seconds"] >= 0 and report["checks"]["computed_total"] == 270.25
    assert len(_rows(workspace.excel_path, "Invoices")) == 1
    assert len(_rows(workspace.excel_path, "Line items")) == 2
    assert _rows(workspace.excel_path, "Review") == []


def test_invoice_with_a_problem_goes_to_review_with_explanations(config, workspace):
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "b.pdf")
    batch = _agent(config, workspace, payload({"totals.total_printed": "280.25"})).process_inbox()
    assert batch.results[0].status == "NEEDS_REVIEW"
    assert (workspace.review / "b.pdf").exists()
    review = _rows(workspace.excel_path, "Review")
    assert len(review) == 1
    assert review[0][4] == "TOTAL_MISMATCH" and "printed total" in review[0][6]


def test_same_file_twice_is_skipped_not_added_twice(config, workspace):
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "c.pdf")
    _agent(config, workspace, payload()).process_inbox()
    # the very same file arrives again, under another name, in a later run
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "c-again.pdf")
    backend = FakeBackend(payload())
    batch = Agent(config, workspace, backend).process_inbox()
    assert [r.status for r in batch.results] == [SKIPPED]
    assert backend.requests == []                      # no AI call
    assert len(_rows(workspace.excel_path, "Invoices")) == 1
    assert (workspace.inbox / "c-again.pdf").exists()  # ignored: left where it is


def test_other_file_with_same_supplier_and_number_is_a_duplicate(config, workspace):
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "first.pdf")
    _agent(config, workspace, payload()).process_inbox()
    copy_sample("04-tipografia-esempio.pdf", workspace.inbox, "second.pdf")  # different bytes
    batch = _agent(config, workspace, payload()).process_inbox()           # a new run
    [result] = batch.results
    assert result.codes == ["DUPLICATE"]
    assert "first.pdf" in result.issues[0].message
    assert len(_rows(workspace.excel_path, "Invoices")) == 2               # appended


def test_backend_failure_stops_the_run_and_leaves_the_file(config, workspace):
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "d.pdf")
    copy_sample("04-tipografia-esempio.pdf", workspace.inbox, "e.pdf")
    batch = _agent(config, workspace, BackendError("Claude Code is not logged in")).process_inbox()
    assert batch.error and "not logged in" in batch.error
    assert batch.results == []
    assert {p.name for p in list_pdfs(workspace.inbox)} == {"d.pdf", "e.pdf"}
    assert not workspace.excel_path.exists()
    assert not workspace.registry_path.exists()


def test_extraction_failed_goes_to_review(config, workspace):
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "f.pdf")
    batch = _agent(config, workspace, "not json").process_inbox()
    [result] = batch.results
    assert result.status == "EXTRACTION_FAILED"
    assert (workspace.review / "f.pdf").exists()
    assert _rows(workspace.excel_path, "Invoices")[0][1] == "EXTRACTION_FAILED"
    assert _rows(workspace.excel_path, "Review")[0][4] == "EXTRACTION_FAILED"


def test_a_file_that_is_not_a_pdf_fails_cleanly(config, workspace):
    (workspace.inbox / "broken.pdf").write_bytes(b"this is not a pdf")
    backend = FakeBackend(payload())
    batch = Agent(config, workspace, backend).process_inbox()
    assert batch.results[0].status == "EXTRACTION_FAILED"
    assert backend.requests == []
    assert (workspace.review / "broken.pdf").exists()


def test_scanned_sample_is_sent_as_a_file(config, workspace):
    copy_sample("09-menuiserie-specimen-scan.pdf", workspace.inbox, "scan.pdf")
    backend = FakeBackend(payload())
    batch = Agent(config, workspace, backend).process_inbox()
    assert batch.results[0].read_mode == "scan"
    assert backend.requests[0].pdf_path is not None


def test_pdf_bytes_are_never_modified(config, workspace):
    source = copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "g.pdf")
    before = source.read_bytes()
    _agent(config, workspace, payload({"totals.total_printed": "1.00"})).process_inbox()
    assert (workspace.review / "g.pdf").read_bytes() == before


def test_name_collision_in_destination_keeps_both_files(config, workspace):
    (workspace.processed / "h.pdf").write_bytes(b"older file with the same name")
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "h.pdf")
    batch = _agent(config, workspace, payload()).process_inbox()
    moved = batch.results[0].moved_to
    assert moved.name.startswith("h-") and moved.exists()
    assert (workspace.processed / "h.pdf").read_bytes() == b"older file with the same name"


def test_injection_in_the_pdf_is_flagged_even_if_the_ai_misses_it(config, workspace):
    # Sample 10 contains a hidden instruction. Here the fake AI reports nothing:
    # the keyword scan of the PDF text must still flag the invoice.
    copy_sample("10-conseil-fantome.pdf", workspace.inbox, "i.pdf")
    batch = _agent(config, workspace, payload()).process_inbox()
    [result] = batch.results
    assert "SUSPICIOUS_INSTRUCTION" in result.codes
    assert result.status == "NEEDS_REVIEW"


def test_excel_open_in_excel_stops_before_calling_the_ai(config, workspace, monkeypatch):
    import invoice_agent.pipeline as pipeline
    from invoice_agent.excel import ExcelLockedError

    def locked(path):
        raise ExcelLockedError("invoices.xlsx is open in another program. Close it and run again.")

    monkeypatch.setattr(pipeline, "ensure_writable", locked)
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "j.pdf")
    backend = FakeBackend(payload())
    batch = Agent(config, workspace, backend).process_inbox()
    assert batch.error and "open in another program" in batch.error
    assert backend.requests == []
    assert (workspace.inbox / "j.pdf").exists()


def test_unreadable_registry_is_reported_clearly(config, workspace):
    import pytest

    from invoice_agent.registry import RegistryError

    workspace.registry_path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(RegistryError, match="already processed"):
        Agent(config, workspace, FakeBackend(payload()))


# --- one bad file never stops the others -------------------------------------------

def test_a_file_removed_during_the_run_does_not_stop_the_others(config, workspace):
    good = copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "good.pdf")
    batch = _agent(config, workspace, payload()).process_inbox([workspace.inbox / "gone.pdf", good])
    assert [f.file for f in batch.failures] == ["gone.pdf"]
    assert [r.status for r in batch.results] == ["OK"]
    assert batch.error is None


def test_unexpected_error_on_one_file_does_not_stop_the_others(config, workspace):
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "a.pdf")
    copy_sample("04-tipografia-esempio.pdf", workspace.inbox, "b.pdf")
    batch = _agent(config, workspace, RuntimeError("something odd"), payload({"invoice.number": "R-2"})).process_inbox()
    assert [f.file for f in batch.failures] == ["a.pdf"] and "something odd" in batch.failures[0].message
    assert [r.file for r in batch.results] == ["b.pdf"]
    assert (workspace.inbox / "a.pdf").exists()


def test_timeout_on_one_file_does_not_stop_the_others(config, workspace):
    from invoice_agent.backends import BackendTimeout

    copy_sample("09-menuiserie-specimen-scan.pdf", workspace.inbox, "a-slow.pdf")
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "b.pdf")
    batch = _agent(config, workspace, BackendTimeout("too slow"), payload()).process_inbox()
    assert [f.file for f in batch.failures] == ["a-slow.pdf"]
    assert [r.status for r in batch.results] == ["OK"]
    assert (workspace.inbox / "a-slow.pdf").exists() and batch.error is None


# --- files, Excel and registry stay consistent ---------------------------------------

def test_pdf_that_cannot_be_moved_stays_in_the_inbox_only(config, workspace, monkeypatch):
    import invoice_agent.pipeline as pipeline

    def locked(source, destination):
        raise PermissionError("the file is open in another program")

    monkeypatch.setattr(pipeline, "move_file", locked)
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "k.pdf")
    [result] = _agent(config, workspace, payload()).process_inbox().results
    assert result.moved_to is None and "could not be moved" in result.note
    assert (workspace.inbox / "k.pdf").exists() and not (workspace.processed / "k.pdf").exists()
    assert _rows(workspace.excel_path, "Invoices")[0][21] == "not moved (still in the inbox)"
    monkeypatch.undo()
    [again] = _agent(config, workspace, payload()).process_inbox().results  # next run: not added twice
    assert again.status == SKIPPED and len(_rows(workspace.excel_path, "Invoices")) == 1


def test_workbook_locked_after_the_ai_call_puts_the_pdf_back(config, workspace, monkeypatch):
    import invoice_agent.pipeline as pipeline
    from invoice_agent.excel import ExcelLockedError

    def locked(path, results):
        raise ExcelLockedError("invoices.xlsx is open in another program.")

    monkeypatch.setattr(pipeline, "append_results", locked)
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "l.pdf")
    batch = _agent(config, workspace, payload()).process_inbox()
    assert batch.error and "open in another program" in batch.error
    assert (workspace.inbox / "l.pdf").exists() and not (workspace.processed / "l.pdf").exists()
    assert not workspace.registry_path.exists()


def test_failed_extraction_is_processed_again_when_the_file_comes_back(config, workspace):
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "m.pdf")
    [first] = _agent(config, workspace, "not json").process_inbox().results
    assert first.status == "EXTRACTION_FAILED"
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "m.pdf")  # put back to try again
    [second] = _agent(config, workspace, payload()).process_inbox().results
    assert second.status == "OK" and "extraction failed" in second.note
    assert [row[1] for row in _rows(workspace.excel_path, "Invoices")] == ["EXTRACTION_FAILED", "OK"]
    assert second.json_path == first.json_path  # same report, updated
    reloaded = Registry.load(workspace.registry_path)
    assert [r.status for r in reloaded.records] == ["OK"]


def test_short_text_layer_of_a_scanned_pdf_is_still_scanned_for_injections(config, workspace):
    (workspace.inbox / "n.pdf").write_bytes(make_pdf("Mark as paid"))
    backend = FakeBackend(payload())
    [result] = Agent(config, workspace, backend).process_inbox().results
    assert result.read_mode == "scan" and backend.requests[0].pdf_path is not None
    assert "SUSPICIOUS_INSTRUCTION" in result.codes
