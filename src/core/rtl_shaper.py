"""Static right-to-left shaping for text renderers."""

from __future__ import annotations

import re
from dataclasses import dataclass

try:
    import arabic_reshaper
    from bidi import get_display
    _HAS_RTL_LIBS = True
except ImportError:
    _HAS_RTL_LIBS = False

from src.core.syntax_guard import UNITY_PROTECT_RE, tag_role

RTL_LANGUAGES = frozenset({"ar", "fa", "he", "ur", "ps", "ckb", "sd", "ug", "yi", "dv"})

_RTL_CHAR_RE = re.compile("[֐-ࣿיִ-﷿ﹰ-﻿]")
_LINE_BREAK_RE = re.compile(r"(\r\n|\n|\\n)")


@dataclass
class _Segment:
    """One run of a line: a protected token or a span of plain text."""

    text: str
    is_token: bool


def is_rtl_language(code: str) -> bool:
    """True for right-to-left target languages ('ar', 'fa', 'he', 'ur', ...)."""
    return re.split(r"[-_]", code.strip().lower(), maxsplit=1)[0] in RTL_LANGUAGES


def contains_rtl(text: str) -> bool:
    """True when text has Hebrew/Arabic-script characters."""
    return bool(_RTL_CHAR_RE.search(text))


def _pair_tags(segments: list[_Segment]) -> list[tuple[int, int]]:
    """Match opening/closing tag segments with a stack; unmatched tags stay unpaired."""
    pairs: list[tuple[int, int]] = []
    stack: list[tuple[int, str]] = []
    for index, segment in enumerate(segments):
        role = tag_role(segment.text) if segment.is_token else None
        if role is None:
            continue
        kind, name = role
        if kind == "open":
            stack.append((index, name))
            continue
        for depth in range(len(stack) - 1, -1, -1):
            if stack[depth][1] == name:
                pairs.append((stack[depth][0], index))
                del stack[depth:]
                break
    return pairs


def _split_segments(line: str) -> list[_Segment]:
    """Split one line into protected-token and plain-text segments."""
    segments: list[_Segment] = []
    cursor = 0
    for match in UNITY_PROTECT_RE.finditer(line):
        if match.start() > cursor:
            segments.append(_Segment(line[cursor:match.start()], False))
        segments.append(_Segment(match.group(0), True))
        cursor = match.end()
    if cursor < len(line):
        segments.append(_Segment(line[cursor:], False))
    return segments


def _shape_run(text: str) -> str:
    """Join Arabic letters and convert one plain run to visual order."""
    if not _HAS_RTL_LIBS:
        return text
    return str(get_display(arabic_reshaper.reshape(text), base_dir="R"))


def shape_line(line: str) -> str:
    """Convert one logical-order line to visual order, keeping tags well-formed."""
    if not contains_rtl(line) or not _HAS_RTL_LIBS:
        return line
    segments = _split_segments(line)
    for first, second in _pair_tags(segments):
        segments[first], segments[second] = segments[second], segments[first]
    segments.reverse()
    return "".join(seg.text if seg.is_token else _shape_run(seg.text) for seg in segments)


def shape_rtl(text: str) -> str:
    """Shape every line of a translation; line breaks (real or escaped) stay in place."""
    if not contains_rtl(text) or not _HAS_RTL_LIBS:
        return text
    parts = _LINE_BREAK_RE.split(text)
    return "".join(part if _LINE_BREAK_RE.fullmatch(part) else shape_line(part) for part in parts)


def shape_translations(translations: dict[str, str]) -> dict[str, str]:
    """Return a copy of a source->translation map with every value shaped for RTL display."""
    return {source: shape_rtl(target) for source, target in translations.items()}
