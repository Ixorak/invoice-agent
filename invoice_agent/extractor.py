"""AI extraction with validation and one retry.

1. Ask the backend for JSON that follows the schema.
2. Validate it with pydantic.
3. If it is invalid, ask once more and send the validation errors back.
4. If it is still invalid, the extraction has failed (status EXTRACTION_FAILED).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .backends import Backend, ExtractionRequest, InvalidAnswer
from .models import InvoiceExtraction, ai_json_schema
from .pdf_reader import PdfContent
from .prompts import SYSTEM_PROMPT, retry_prompt, scanned_prompt, text_prompt

MAX_ATTEMPTS = 2  # first try + one retry


@dataclass
class Attempt:
    number: int
    seconds: float
    ok: bool
    error: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class ExtractionOutcome:
    extraction: InvoiceExtraction | None
    attempts: list[Attempt]
    error: str | None = None
    raw_answer: str | None = None

    @property
    def ai_seconds(self) -> float:
        return sum(a.seconds for a in self.attempts)


def parse_json_answer(text: str) -> object:
    """Parse the AI's text answer. Tolerates a ```json fence around the object."""
    s = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", s, flags=re.S)
    if fence:
        s = fence.group(1)
    return json.loads(s)


def describe_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors()[:20]:
        where = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"- {where}: {err['msg']}")
    return "\n".join(lines)


class Extractor:
    def __init__(self, backend: Backend, max_attempts: int = MAX_ATTEMPTS):
        self.backend = backend
        self.max_attempts = max_attempts
        self.schema = ai_json_schema()

    def extract(self, content: PdfContent, pdf_path: Path) -> ExtractionOutcome:
        if content.scanned:
            base_prompt, attachment = scanned_prompt(), pdf_path
        else:
            base_prompt, attachment = text_prompt(content.text), None

        attempts: list[Attempt] = []
        prompt = base_prompt
        last_error, last_answer = None, None
        for number in range(1, self.max_attempts + 1):
            request = ExtractionRequest(SYSTEM_PROMPT, prompt, self.schema, attachment)
            try:
                reply = self.backend.complete(request)  # BackendError propagates: nothing is decided
            except InvalidAnswer as exc:
                last_error, last_answer = str(exc), exc.raw
                attempts.append(Attempt(number, 0.0, False, last_error))
            else:
                last_answer = reply.text
                try:
                    data = reply.data if reply.data is not None else parse_json_answer(reply.text)
                    extraction = InvoiceExtraction.from_ai(data)
                except json.JSONDecodeError as exc:
                    last_error = f"- the answer is not valid JSON: {exc}"
                except ValidationError as exc:
                    last_error = describe_validation_error(exc)
                else:
                    attempts.append(Attempt(number, reply.seconds, True, None, reply.meta))
                    return ExtractionOutcome(extraction, attempts, None, reply.text)
                attempts.append(Attempt(number, reply.seconds, False, last_error, reply.meta))
            prompt = retry_prompt(base_prompt, last_answer or "", last_error or "unknown error")

        return ExtractionOutcome(
            None, attempts,
            f"The AI answer was still invalid after {self.max_attempts} attempts:\n{last_error}",
            last_answer,
        )
