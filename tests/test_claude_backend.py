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
