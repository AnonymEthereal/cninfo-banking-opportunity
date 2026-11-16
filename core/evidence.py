"""Verbatim evidence matching that ignores typography, not wording.

The project promises that every quote can be traced back to the announcement.
Models are usually faithful but occasionally change quote style, full-width
punctuation or dash characters. Normalising those differences keeps the
traceability guarantee while avoiding failures that are purely typographic.
Paraphrases, merged sentences and ellipsis abbreviations still fail.
"""

from __future__ import annotations

import re
import unicodedata

# Quote variants are equivalent when the wording is identical.
_QUOTES = {
    0x2018: "'",
    0x2019: "'",
    0x201A: "'",
    0x201B: "'",
    0x201C: '"',
    0x201D: '"',
    0x201E: '"',
    0x201F: '"',
    0x2032: "'",
    0x2033: '"',
    0x300C: '"',
    0x300D: '"',
    0x300E: '"',
    0x300F: '"',
    0xFF02: '"',
    0xFF07: "'",
}

_DASHES = dict.fromkeys(
    (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xFE58, 0xFE63, 0xFF0D),
    "-",
)

# Invisible characters produced by PDF extraction or copy/paste.
_INVISIBLE = dict.fromkeys(
    (0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF),
    None,
)

_PUNCTUATION = {
    0x3001: ",",  # 、
    0x3002: ".",  # 。
    0xFF61: ".",  # ｡
}

_TRANSLATION = {**_QUOTES, **_DASHES, **_INVISIBLE, **_PUNCTUATION}
_WHITESPACE = re.compile(r"\s+")


def normalize(value: str) -> str:
    """Return a comparison form that keeps every character that carries meaning."""
    text = unicodedata.normalize("NFKC", value or "")
    text = text.translate(_TRANSLATION)
    return _WHITESPACE.sub("", text)


def preview(value: str | None, limit: int = 120) -> str:
    """Compact a quote for error details and repair prompts."""
    text = _WHITESPACE.sub(" ", (value or "").strip())
    return text if len(text) <= limit else text[:limit] + "…"


class EvidenceMatcher:
    """Pre-normalise the source once, then match any number of quotes."""

    def __init__(self, source: str):
        self.source = normalize(source)

    def contains(self, quote: str | None) -> bool:
        text = normalize(quote or "")
        if not text:
            return False
        if text in self.source:
            return True
        # A model may wrap an otherwise exact quote in extra quotation marks.
        if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
            return text[1:-1] in self.source
        return False
