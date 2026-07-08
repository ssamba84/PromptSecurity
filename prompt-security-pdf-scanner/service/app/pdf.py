"""PDF text extraction — one capture-source adapter, decoupled from any inspector.

Turns raw PDF bytes into plain text. This is intentionally the *only* place that
knows about PDFs; inspectors only ever see normalized text/messages.
"""
from __future__ import annotations

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PdfReadError

logger = logging.getLogger("inspection.pdf")


class PdfExtractionError(Exception):
    """Raised when a file cannot be parsed as a PDF at all."""


def extract_text(data: bytes) -> str:
    """Extract the concatenated text layer from a PDF.

    Returns an empty string for PDFs with no extractable text (e.g. scanned /
    image-only documents) — the caller decides how to handle that. Raises
    PdfExtractionError if the bytes are not a parseable PDF.
    """
    try:
        reader = PdfReader(io.BytesIO(data))
    except (PdfReadError, OSError, ValueError) as exc:
        raise PdfExtractionError(f"Could not read PDF: {exc}") from exc

    # Encrypted PDFs: try an empty-password decrypt (common for "owner" locks).
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001 - pypdf raises assorted errors here
            logger.warning("PDF is encrypted and could not be decrypted with an empty password")
            return ""

    parts: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            parts.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 - never fail the whole doc on one bad page
            logger.warning("Failed to extract text from page %d: %s", i, exc)

    text = "\n".join(p for p in parts if p).strip()
    if not text:
        logger.info("No extractable text found in PDF (%d pages) — likely scanned/image-only", len(reader.pages))
    return text
