"""The Claude Code backend without calling Claude: command line, output parsing, discovery."""

import json
from pathlib import Path

import pytest

from invoice_agent.backends import BackendError, BackendUnavailable, ExtractionRequest, InvalidAnswer
from invoice_agent.backends.claude_code import ClaudeCodeBackend, find_claude, parse_envelope


@pytest.fixture
def backend(tmp_path):
    fake_exe = tmp_path / "claude.exe"
    fake_exe.write_bytes(b"")
    return ClaudeCodeBackend(claude_path=str(fake_exe), model="sonnet", timeout_seconds=30)


def _option(cmd, name):
    return cmd[cmd.index(name) + 1]


def test_text_invoice_gets_no_tool_at_all(backend):
    cmd = backend.build_command(ExtractionRequest("system", "user", {"type": "object"}))
    assert _option(cmd, "--tools") == ""
    assert _option(cmd, "--permission-mode") == "dontAsk"
    for flag in ("-p", "--safe-mode", "--restricted", "--strict-mcp-config", "--disable-slash-commands",
                 "--no-session-persistence"):
        assert flag in cmd
    assert _option(cmd, "--system-prompt") == "system"
    assert json.loads(_option(cmd, "--json-schema")) == {"type": "object"}
    assert _option(cmd, "--model") == "sonnet"


def test_scanned_invoice_gets_only_the_read_tool(backend):
    cmd = backend.build_command(ExtractionRequest("s", "u", {}, pdf_path=Path("scan.pdf")))
    assert _option(cmd, "--tools") == "Read"


def _envelope(**fields):
    base = {"type": "result", "subtype": "success", "is_error": False, "result": "{}", "num_turns": 2,
            "duration_ms": 4000, "usage": {"input_tokens": 10, "output_tokens": 5},
            "modelUsage": {"claude-sonnet-x": {}}, "permission_denials": []}
    base.update(fields)
    return json.dumps(base).encode()


def test_structured_output_is_used():
    reply = parse_envelope(0, _envelope(structured_output={"a": 1}, result='{"a":1}'), b"", 1.0)
    assert reply.data == {"a": 1}
    assert reply.meta["model"] == "claude-sonnet-x"


def test_error_result_raises_backend_error():
    with pytest.raises(BackendError):
        parse_envelope(1, _envelope(is_error=True, subtype="error_during_execution", result="Not logged in"), b"", 1.0)


def test_schema_retries_exhausted_is_an_invalid_answer():
    with pytest.raises(InvalidAnswer):
        parse_envelope(1, _envelope(is_error=True, subtype="error_max_structured_output_retries"), b"", 1.0)


def test_no_json_at_all_raises_backend_error():
    with pytest.raises(BackendError):
        parse_envelope(1, b"", b"claude: command failed", 1.0)


def test_npm_script_launcher_is_refused(tmp_path):
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off")
    with pytest.raises(BackendUnavailable):
        find_claude(str(shim))


def test_configured_path_must_exist(tmp_path):
    with pytest.raises(BackendUnavailable):
        find_claude(str(tmp_path / "missing" / "claude.exe"))


def test_failure_to_start_claude_is_a_temporary_error(backend):
    # The fake claude.exe is an empty file: Windows cannot start it (e.g. like during an update).
    from invoice_agent.backends import BackendError

    with pytest.raises(BackendError) as info:
        backend.complete(ExtractionRequest("s", "u", {}))
    assert not isinstance(info.value, BackendUnavailable)


def test_timeout_is_reported_as_a_timeout(backend, monkeypatch):
    import subprocess

    from invoice_agent.backends import BackendTimeout
    from invoice_agent.backends import claude_code

    def too_slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=30)

    monkeypatch.setattr(claude_code.subprocess, "run", too_slow)
    with pytest.raises(BackendTimeout):
        backend.complete(ExtractionRequest("s", "u", {}))


def test_each_call_runs_in_an_empty_folder_inside_the_work_root(tmp_path, monkeypatch):
    import subprocess

    from invoice_agent.backends import claude_code

    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"")
    work_root = tmp_path / "output" / ".work"
    (work_root / "invoice-agent-leftover").mkdir(parents=True)  # from a run that was killed
    backend = ClaudeCodeBackend(claude_path=str(exe), work_root=work_root)
    assert not (work_root / "invoice-agent-leftover").exists()

    seen = {}
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    def fake_run(cmd, **kwargs):
        folder = Path(kwargs["cwd"])
        seen["folder"], seen["files"] = folder, sorted(p.name for p in folder.iterdir())
        envelope = _envelope(structured_output={"a": 1})
        return subprocess.CompletedProcess(cmd, 0, envelope, b"")

    monkeypatch.setattr(claude_code.subprocess, "run", fake_run)
    backend.complete(ExtractionRequest("s", "u", {}, pdf_path=pdf))
    assert seen["folder"].parent == work_root
    assert seen["files"] == ["invoice.pdf"]       # the copy of this one PDF, nothing else
    assert not seen["folder"].exists()             # removed after the call
