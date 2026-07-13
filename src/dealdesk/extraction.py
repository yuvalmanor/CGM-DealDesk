"""Extraction Ladder — ``Email -> [property_fields]``.

The tracer-bullet ladder has two rungs (Source Templates are added in Phase 8+):

1. **generic heuristics** over the combined email body + attached-PDF text;
2. **AI fallback** (behind the ``AiFallback`` interface) when the heuristics
   can't recover the Buy Box's must-have fields, or for an unknown Source.

Gathering body *and* PDF text before either rung means facts in a property-report
PDF are never ignored. The AI rung is the only one that finds multiple Properties
in one Email; the heuristics recover a single Property's fields.
"""

from __future__ import annotations

from .ai_fallback import AiFallback
from .buybox import BuyBox
from .heuristics import generic_extract
from .models import Email
from .pdf import extract_pdf_text

_PDF_MIME = "application/pdf"


class ExtractionLadder:
    def __init__(self, buybox: BuyBox, ai: AiFallback, pdf_to_text=extract_pdf_text):
        self._buybox = buybox
        self._ai = ai
        self._pdf_to_text = pdf_to_text

    def extract(self, email: Email) -> list[dict]:
        text = self._gather_text(email)
        if not text.strip():
            return []

        heuristic = generic_extract(text)
        if self._has_must_haves(heuristic):
            return [heuristic]

        # Deterministic extraction fell short — hand the whole text to the AI
        # fallback (handles unknown Sources and multi-Property Emails).
        return self._ai.extract_properties(text)

    def _gather_text(self, email: Email) -> str:
        parts = [email.subject or "", email.body_text or ""]
        for att in email.attachments:
            if att.mime_type == _PDF_MIME or att.filename.lower().endswith(".pdf"):
                parts.append(self._pdf_to_text(att.data))
        return "\n".join(p for p in parts if p)

    def _has_must_haves(self, fields: dict) -> bool:
        return all(
            fields.get(name) not in (None, "") for name in self._buybox.must_have_names()
        )
