"""AI backends.

A backend receives a system prompt, a user prompt, a JSON schema and, for a
scanned invoice, the PDF to read. It returns the AI's raw answer. Everything
else (validation, retry, rules) is independent of the backend.

Only the Claude Code backend is implemented. Another one (for example the
Anthropic API) can be added by subclassing :class:`Backend` and registering
it in :func:`create_backend`.
"""

from __future__ import annotations

from pathlib import Path

from .base import (
    Backend,
    BackendError,
    BackendReply,
    BackendTimeout,
    BackendUnavailable,
    ExtractionRequest,
    InvalidAnswer,
)

__all__ = [
    "Backend", "BackendError", "BackendReply", "BackendTimeout", "BackendUnavailable", "ExtractionRequest",
    "InvalidAnswer", "create_backend",
]


def create_backend(config, work_root: Path | None = None) -> Backend:
    """``work_root``: folder for the temporary files of each call (inside the agent's output folder)."""
    if config.backend == "claude-code":
        from .claude_code import ClaudeCodeBackend

        return ClaudeCodeBackend(
            claude_path=config.claude_path or None,
            model=config.model,
            effort=config.effort,
            timeout_seconds=config.timeout_seconds,
            work_root=work_root,
        )
    raise BackendUnavailable(f"Unknown backend '{config.backend}'. Only 'claude-code' is implemented.")
