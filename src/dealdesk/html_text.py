"""HTML email body -> plain text (pure).

Many Sources send **HTML-only** Emails — a single ``text/html`` part with no
``text/plain`` alternative. Their facts live in a table-based marketing layout,
so until this rung existed the Extraction Ladder saw nothing but the subject
line for roughly a quarter of live volume.

Stdlib ``html.parser`` only: what's needed here is tag-stripping that preserves
block boundaries, not rendering, so a heavyweight dependency would buy nothing.
Attribute values (the ``href`` bulk of a marketing email) are never emitted —
``handle_data`` sees text nodes alone.

Collapsing whitespace is load-bearing for **cost**, not just tidiness: a 70KB
marketing email reduces to a couple of KB of text before it reaches the AI rung.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Content of these never carries deal facts. All are reliably paired in real
# email HTML; void tags (meta/link) are deliberately absent — they hold no text
# node, and an unpaired skip tag would swallow the rest of the document.
_SKIP_TAGS = frozenset({"script", "style", "head"})

# Boundaries that separate facts onto their own lines.
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "tr", "table", "li", "ul", "ol", "blockquote",
        "section", "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
    }
)

# Table cells sit *beside* each other ("Price" | "$250,000"), so they need a
# separator but not a line break — the heuristics' label-then-value patterns
# tolerate a space far better than a newline splitting a label from its value.
_CELL_TAGS = frozenset({"td", "th"})

# Every horizontal whitespace kind incl. nbsp (&nbsp; is ubiquitous in email
# HTML); newline excluded so line structure survives until it's normalized.
_H_SPACE_RUN = re.compile(r"[ \t\r\f\v\xa0\u2007\u202f\u200b\ufeff]+")
_BLANK_RUN = re.compile(r"\n{3,}")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")
        elif tag in _CELL_TAGS:
            self._chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")
        elif tag in _CELL_TAGS:
            self._chunks.append(" ")

    def handle_startendtag(self, tag: str, attrs) -> None:
        # ``<br/>``: one boundary, where the default start+end pair would emit
        # two. A self-closing skip tag encloses nothing, so it must not touch
        # the skip depth — an unbalanced increment would swallow the rest of the
        # document.
        if tag not in _SKIP_TAGS:
            self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)

    @property
    def text(self) -> str:
        return "".join(self._chunks)


def html_to_text(raw: str) -> str:
    """Return the visible text of an HTML body, whitespace-normalized.

    Best-effort, mirroring the PDF extractor: malformed markup contributes
    whatever text was recovered before the parser gave up rather than failing
    the Email's whole run (which would only park it in ``Error`` forever)."""
    if not raw or not raw.strip():
        return ""

    parser = _TextExtractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup must not sink the run
        pass

    text = _H_SPACE_RUN.sub(" ", parser.text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_RUN.sub("\n\n", text).strip()
