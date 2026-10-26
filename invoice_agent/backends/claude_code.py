"""Backend that runs the Claude Code CLI installed on this computer (`claude -p`).

It uses the account Claude Code is logged in with: no API key is needed.
Every option used here exists in Claude Code 2.1.x (checked with `claude --help`):

  -p --output-format json      one JSON result on stdout
  --json-schema <schema>       Claude Code validates the answer against our schema
  --system-prompt <prompt>     replaces Claude Code's default (coding) system prompt
  --tools ""                   no tool at all (text invoice)
  --tools Read                 only the Read tool (scanned invoice), see below
  --permission-mode dontAsk    anything not allowed is refused, nobody is asked
  --permission-prompts none
  --safe-mode                  no CLAUDE.md, skills, plugins, hooks or MCP servers
  --strict-mcp-config          no MCP server
  --disable-slash-commands     no skill
  --restricted                 user / project settings ignored, file tools confined
                               to the working folder
  --no-session-persistence     nothing is saved in the user's session history

Each call runs in a new, empty temporary folder, created inside the agent's
own output folder (output/.work/). For a scanned invoice, a copy of the PDF is
placed there as invoice.pdf: it is the only file the AI can read (reads
outside the folder are refused). The folder is deleted after the call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ..prompts import SCANNED_FILE_NAME
from .base import (
    Backend,
    BackendError,
    BackendReply,
    BackendTimeout,
    BackendUnavailable,
    ExtractionRequest,
    InvalidAnswer,
)

WORK_PREFIX = "invoice-agent-"

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no console window pops up


def default_locations() -> list[Path]:
    home = Path.home()
    if os.name == "nt":
        return [home / ".local" / "bin" / "claude.exe"]
    return [home / ".local" / "bin" / "claude", home / ".claude" / "local" / "claude"]


def _native_only(path: Path) -> Path:
    if path.suffix.lower() in (".cmd", ".bat", ".ps1"):
        raise BackendUnavailable(
            f"{path} is a script launcher (npm install). invoice-agent needs the native claude executable, "
            "because the prompt and the JSON schema cannot be passed safely through cmd.exe. "
            "Install the native build (`claude install`) or set claude_path in config.json to claude.exe."
        )
    return path


def find_claude(configured: str | None = None) -> Path:
    """Find the claude executable: config.json, then PATH, then the default install folder."""
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise BackendUnavailable(f"claude_path in config.json does not point to a file: {path}")
        return _native_only(path)
    names = ["claude.exe", "claude"] if os.name == "nt" else ["claude"]
    found = shutil.which(names[0])
    if found:
        return _native_only(Path(found))
    for candidate in default_locations():
        if candidate.is_file():
            return candidate
    for name in names[1:]:
        found = shutil.which(name)
        if found:
            return _native_only(Path(found))
    raise BackendUnavailable(
        "Claude Code was not found. Install it, log in once by running `claude`, "
        "or set claude_path in config.json."
    )


def parse_envelope(returncode: int, stdout: bytes, stderr: bytes, seconds: float) -> BackendReply:
    """Read the JSON printed by `claude -p --output-format json`."""
    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    envelope = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and candidate.get("type") == "result":
                envelope = candidate
                break
    if envelope is None:
        detail = err or out or "no output"
        raise BackendError(f"Claude Code exited with code {returncode} without a result: {detail[:500]}")

    subtype = envelope.get("subtype")
    if envelope.get("is_error") or subtype != "success":
        message = envelope.get("result") or envelope.get("errors") or err or "no details"
        if subtype == "error_max_structured_output_retries":
            raise InvalidAnswer(f"Claude Code could not produce JSON that matches the schema: {message}", raw=str(message))
        raise BackendError(f"Claude Code reported an error ({subtype}): {str(message)[:500]}")
    if returncode != 0:
        raise BackendError(f"Claude Code exited with code {returncode}: {(err or out)[:500]}")

    data = envelope.get("structured_output")
    text = envelope.get("result") or (json.dumps(data, ensure_ascii=False) if data is not None else "")
    usage = envelope.get("usage") or {}
    meta = {
        "model": ", ".join((envelope.get("modelUsage") or {}).keys()) or None,
        "duration_ms": envelope.get("duration_ms"),
        "num_turns": envelope.get("num_turns"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "refused_tool_calls": len(envelope.get("permission_denials") or []),
    }
    return BackendReply(text=text, data=data if isinstance(data, dict) else None, seconds=seconds, meta=meta)


class ClaudeCodeBackend(Backend):
    name = "claude-code"

    def __init__(self, claude_path: str | None = None, model: str | None = "sonnet",
                 effort: str | None = None, timeout_seconds: float = 240, work_root: Path | None = None):
        self.executable = find_claude(claude_path)
        self.model = model
        self.effort = effort
        self.timeout_seconds = timeout_seconds
        self.work_root = Path(work_root) if work_root else None
        self._version: str | None = None
        if self.work_root is not None:
            self.work_root.mkdir(parents=True, exist_ok=True)
            for leftover in self.work_root.glob(WORK_PREFIX + "*"):  # from a run that was killed
                shutil.rmtree(leftover, ignore_errors=True)

    def version(self) -> str:
        if self._version is None:
            try:
                proc = subprocess.run([str(self.executable), "--version"], capture_output=True, timeout=60,
                                      stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise BackendUnavailable(f"Cannot run {self.executable}: {exc}") from exc
            text = proc.stdout.decode("utf-8", errors="replace").strip()
            if proc.returncode != 0 or not text:
                raise BackendUnavailable(f"`claude --version` failed: {proc.stderr.decode('utf-8', 'replace')[:300]}")
            self._version = text.split()[0]
        return self._version

    def describe(self) -> dict:
        return {
            "backend": self.name,
            "claude_code_version": self.version(),
            "model_setting": self.model,
            "effort": self.effort,
            "executable": str(self.executable),
        }

    def build_command(self, request: ExtractionRequest) -> list[str]:
        tools = "Read" if request.pdf_path else ""
        cmd = [
            str(self.executable), "-p",
            "--output-format", "json",
            "--no-session-persistence",
            "--safe-mode",
            "--restricted",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--permission-mode", "dontAsk",
            "--permission-prompts", "none",
            "--tools", tools,
            "--system-prompt", request.system_prompt,
            "--json-schema", json.dumps(request.json_schema, ensure_ascii=False, separators=(",", ":")),
        ]
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["--effort", self.effort]
        return cmd

    def complete(self, request: ExtractionRequest) -> BackendReply:
        workdir = Path(tempfile.mkdtemp(prefix=WORK_PREFIX, dir=self.work_root))
        try:
            if request.pdf_path is not None:
                shutil.copyfile(request.pdf_path, workdir / SCANNED_FILE_NAME)
            started = time.perf_counter()
            try:
                proc = subprocess.run(
                    self.build_command(request),
                    input=request.user_prompt.encode("utf-8"),
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    cwd=workdir,
                    creationflags=_NO_WINDOW,
                )
            except subprocess.TimeoutExpired as exc:
                raise BackendTimeout(
                    f"Claude Code did not answer within {self.timeout_seconds:.0f} s "
                    "(timeout_seconds in config.json)."
                ) from exc
            except OSError as exc:  # e.g. claude.exe is being updated: try again later
                raise BackendError(f"Cannot start Claude Code ({self.executable}): {exc}") from exc
            seconds = time.perf_counter() - started
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        return parse_envelope(proc.returncode, proc.stdout, proc.stderr, seconds)


if __name__ == "__main__":  # pragma: no cover - manual check: python -m invoice_agent.backends.claude_code
    backend = ClaudeCodeBackend()
    print(json.dumps(backend.describe(), indent=2), file=sys.stdout)
