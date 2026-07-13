"""PDF text extractor.

Pulls plain text out of an attached property-report PDF so its facts feed the
Extraction Ladder alongside the email body. ``pypdf`` is imported lazily so the
test suite (which fakes extraction) doesn't require it installed.
"""

from __future__ import annotations

import io


def extract_pdf_text(data: bytes) -> str:
    """Return the concatenated text of every page. Best-effort: a page that
    can't be parsed contributes nothing rather than failing the whole run."""
    try:
        from pypdf import PdfReader  # lazy: only needed at runtime
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "pypdf is required to read PDF attachments; add it to the runtime "
            "environment (pip install pypdf)."
        ) from exc

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text:
            parts.append(text)
    return "\n".join(parts)
