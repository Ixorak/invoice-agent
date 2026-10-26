"""Local text extraction. The PDF is opened read-only and never modified."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pdfplumber


class PdfReadError(Exception):
    """The file could not be opened as a PDF."""


@dataclass(frozen=True)
class PdfContent:
    text: str          # text layer; for a scanned invoice it is empty or very short
    pages: int
    scanned: bool      # True when there is (almost) no text layer: the AI reads the file instead
    truncated: bool = False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_pdf(path: Path, min_text_chars: int = 25, max_text_chars: int = 60000) -> PdfContent:
    """Extract the text layer with pdfplumber.

    A PDF with fewer than ``min_text_chars`` letters or digits is treated as scanned:
    the AI will then read the file itself. Its short text layer is kept anyway, so the
    keyword scan still sees it.
    """
    try:
        with pdfplumber.open(path) as pdf:
            pages = [(page.extract_text() or "") for page in pdf.pages]
    except Exception as exc:  # pdfminer raises many different exception types
        raise PdfReadError(f"cannot open the PDF: {exc}") from exc

    if len(pages) > 1:
        text = "\n\n".join(f"--- Page {i} of {len(pages)} ---\n{t}" for i, t in enumerate(pages, 1))
    else:
        text = pages[0] if pages else ""
    meaningful = sum(ch.isalnum() for ch in text)
    scanned = meaningful < min_text_chars
    truncated = len(text) > max_text_chars
    if truncated:
        text = text[:max_text_chars] + "\n[... text truncated ...]"
    return PdfContent(text=text, pages=len(pages), scanned=scanned, truncated=truncated)
