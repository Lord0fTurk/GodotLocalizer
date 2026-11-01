"""Shape checks that tell player-facing sentences from identifiers, paths and numbers."""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_IDENTIFIER_RE = re.compile(r"^[\w./:\\-]+$")
_PUNCTUATION_RE = re.compile(r"[.!?,;:…。！？]")


def looks_like_prose(value: str) -> bool:
    """Two or more words, mostly letters, and not an identifier, path or number list."""
    text = value.strip()
    if len(text) < 6 or text.startswith(("res://", "user://", "http")) or _IDENTIFIER_RE.match(text):
        return False
    words = _WORD_RE.findall(text)
    letters = sum(ch.isalpha() for ch in text)
    return len(words) >= 2 and letters / len(text) >= 0.6


def looks_like_sentence(value: str) -> bool:
    """Prose that reads as a sentence: three or more words, or punctuation. Two capitalised words are a name."""
    text = value.strip()
    return looks_like_prose(text) and (len(_WORD_RE.findall(text)) >= 3 or bool(_PUNCTUATION_RE.search(text)))
