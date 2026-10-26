from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


class BackendUnavailable(Exception):
    """The backend cannot be used at all (e.g. the claude executable is not found)."""


class BackendError(Exception):
    """A call failed (crash, timeout, not logged in...). The invoice is left in the inbox."""


class InvalidAnswer(Exception):
    """The AI answered, but not with usable JSON. Counts as a failed extraction attempt."""

    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw


@dataclass(frozen=True)
class ExtractionRequest:
    system_prompt: str
    user_prompt: str
    json_schema: dict
    pdf_path: Path | None = None  # only for scanned invoices: the one file the AI may read


@dataclass
class BackendReply:
    text: str                       # the answer as text
    data: dict | None               # the answer parsed as JSON by the backend, if available
    seconds: float
    meta: dict = field(default_factory=dict)


class Backend(ABC):
    name: str = "backend"

    @abstractmethod
    def describe(self) -> dict:
        """Name, version and settings, recorded with every result."""

    @abstractmethod
    def complete(self, request: ExtractionRequest) -> BackendReply:
        """Run one extraction call. Raises BackendError or InvalidAnswer."""
