"""PDF reading, configuration, demo folder safety, locking, watch loop, evaluation."""

import json
from decimal import Decimal

import pytest

from invoice_agent.config import ConfigError, load_config
from invoice_agent.demo import MARKER, DemoFolderError, reset_demo_folder
from invoice_agent.evaluation import evaluate
from invoice_agent.locking import AlreadyRunning, FolderLock
from invoice_agent.pdf_reader import PdfReadError, read_pdf, sha256_file
from invoice_agent.pipeline import Agent
from invoice_agent.watcher import stable_files, watch

from .conftest import ROOT, SAMPLES, FakeBackend, copy_sample, payload


def test_text_pdf_and_scanned_pdf_are_told_apart():
    text = read_pdf(SAMPLES / "03-nordwerk-demo.pdf")
    assert not text.scanned and "Rechnungsnummer" in text.text
    scan = read_pdf(SAMPLES / "09-menuiserie-specimen-scan.pdf")
    assert scan.scanned and scan.text == ""


def test_not_a_pdf(tmp_path):
    path = tmp_path / "x.pdf"
    path.write_bytes(b"hello")
    with pytest.raises(PdfReadError):
        read_pdf(path)


def test_sha256_is_stable():
    assert sha256_file(SAMPLES / "01-papeterie-lumenta.pdf") == sha256_file(SAMPLES / "01-papeterie-lumenta.pdf")
    assert sha256_file(SAMPLES / "01-papeterie-lumenta.pdf") != sha256_file(SAMPLES / "08-papeterie-lumenta-duplicata.pdf")


def test_project_config_file_is_valid():
    config, base = load_config(ROOT / "config.json")
    assert config.swiss_vat_rates == [Decimal("8.1"), Decimal("2.6"), Decimal("3.8"), Decimal("0")]
    assert config.tolerance == Decimal("0.05")
    assert base == ROOT


def test_config_errors_are_explicit(tmp_path):
    bad = tmp_path / "config.json"
    bad.write_text(json.dumps({"backend": "anthropic-api"}))
    with pytest.raises(ConfigError, match="only the 'claude-code' backend"):
        load_config(bad)
    bad.write_text(json.dumps({"tolerence": 0.05}))  # typo
    with pytest.raises(ConfigError):
        load_config(bad)


def test_demo_folder_is_only_deleted_if_created_by_the_demo(tmp_path):
    foreign = tmp_path / "my-files"
    foreign.mkdir()
    (foreign / "important.txt").write_text("keep me")
    with pytest.raises(DemoFolderError):
        reset_demo_folder(foreign)
    assert (foreign / "important.txt").exists()

    demo = tmp_path / "demo"
    reset_demo_folder(demo)
    (demo / "old-output.txt").write_text("from a previous demo")
    reset_demo_folder(demo)
    assert (demo / MARKER).exists() and not (demo / "old-output.txt").exists()


def test_only_one_agent_per_folder(tmp_path):
    with FolderLock(tmp_path / ".lock"):
        with pytest.raises(AlreadyRunning):
            FolderLock(tmp_path / ".lock").acquire()
    with FolderLock(tmp_path / ".lock"):  # released: can be taken again
        pass


def test_stable_files_ignores_files_still_growing(tmp_path):
    done, growing = tmp_path / "done.pdf", tmp_path / "growing.pdf"
    done.write_bytes(b"1234")
    growing.write_bytes(b"12")

    def fake_sleep(_):
        growing.write_bytes(b"12345678")

    assert stable_files([done, growing], sleep=fake_sleep) == [done]


def test_watch_processes_new_files(config, workspace):
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "w.pdf")
    agent = Agent(config, workspace, FakeBackend(payload()))
    batches = []
    watch(agent, interval=0.01, on_batch=batches.append, sleep=lambda s: None, max_loops=2)
    assert [r.status for b in batches for r in b.results] == ["OK"]
    assert (workspace.processed / "w.pdf").exists()


def test_evaluation_compares_status_codes_and_fields(config, workspace, tmp_path):
    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "one.pdf")
    batch = Agent(config, workspace, FakeBackend(payload({"totals.total_printed": "1.00"}))).process_inbox()
    expected = {"invoices": [{
        "file": "one.pdf", "language": "de", "scanned": False, "supplier_name": "Muster Handel AG",
        "supplier_vat_id": "CHE-123.456.789", "invoice_number": "R-1001", "invoice_date": "2026-03-02",
        "due_date": "2026-04-01", "currency": "CHF", "line_count": 2, "subtotal": 250.0,
        "vat_breakdown": [{"rate": 8.1, "base": 250.0, "vat": 20.25}], "vat_total": 20.25,
        "total_printed": 1.0, "iban": "CH9300762011623852957",
        "expected_status": "NEEDS_REVIEW", "expected_flags": ["TOTAL_MISMATCH"],
    }]}
    path = tmp_path / "expected.json"
    path.write_text(json.dumps(expected))
    report = evaluate(batch.results, path)
    assert report.status_correct == 1
    assert all(ok == total for _, ok, total in report.field_accuracy())


def test_watch_waits_longer_after_a_failure(config, workspace):
    from invoice_agent.backends import BackendError

    copy_sample("03-nordwerk-demo.pdf", workspace.inbox, "x.pdf")
    agent = Agent(config, workspace, FakeBackend(BackendError("Claude Code is not logged in")))
    waits, batches = [], []

    def fake_sleep(seconds):
        waits.append(seconds)

    watch(agent, interval=10, on_batch=batches.append, sleep=fake_sleep, max_loops=3)
    pauses = [w for w in waits if w != 1.0]  # 1.0 = the "is the file still growing?" pause
    assert pauses == [20, 40]
    assert all(b.error for b in batches)
    assert (workspace.inbox / "x.pdf").exists()


def test_a_second_demo_cannot_empty_a_running_demo(tmp_path):
    from invoice_agent.cli import main

    demo = tmp_path / "demo"
    reset_demo_folder(demo)
    (demo / "inbox").mkdir()
    (demo / "inbox" / "in-progress.pdf").write_bytes(b"%PDF")
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"demo_folder": str(demo)}))
    with FolderLock(tmp_path / ".demo.lock"):  # a demo is running
        assert main(["demo", "--config", str(config_file)]) == 1
    assert (demo / "inbox" / "in-progress.pdf").exists()


def test_watch_does_not_retry_a_failed_file_until_it_changes(config, workspace):
    from invoice_agent.backends import BackendTimeout

    path = copy_sample("09-menuiserie-specimen-scan.pdf", workspace.inbox, "slow.pdf")
    backend = FakeBackend(BackendTimeout("too slow"))
    agent = Agent(config, workspace, backend)
    watch(agent, interval=1, on_batch=lambda b: None, sleep=lambda s: None, max_loops=3)
    assert len(backend.requests) == 1          # tried once, not on every pass
    path.write_bytes(path.read_bytes() + b"\n")  # the file changes (e.g. replaced by a new scan)
    watch(agent, interval=1, on_batch=lambda b: None, sleep=lambda s: None, max_loops=1)
    assert len(backend.requests) == 2
