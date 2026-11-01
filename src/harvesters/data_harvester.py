"""Harvester for plain-text game data: ``.txt`` line files, YAML, ``.cfg``/``.ini`` and text ``.tres`` resources.

Procedural and data-driven games keep their sentences in tables, not in scenes: event templates in a YAML file,
flavour lines in a ``.txt``, an exported ``description`` in a custom ``Resource``. Every format is scanned into
spans inside its own lines, so the very same scan serves harvesting and rewriting and nothing outside a
translated span is touched (comments, indentation, line endings, keys).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from src.harvesters.base import HarvestedEntry, StringSourceType
from src.harvesters.prose import looks_like_sentence
from src.harvesters.scene_harvester import SceneHarvester

KIND_TXT = "txt"
KIND_YAML = "yaml"
KIND_CFG = "cfg"
KIND_TRES = "tres"
DATA_KINDS = frozenset({KIND_TXT, KIND_YAML, KIND_CFG, KIND_TRES})

_GODOT_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"', re.DOTALL)
_GODOT_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|.)", re.DOTALL)
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
_ASSIGNMENT = re.compile(r"^(?P<lead>\s*(?P<prop>[A-Za-z_][\w/.\-]*)\s*=\s*)(?P<value>.*)$", re.DOTALL)
_SECTION = re.compile(r'^\[(?P<name>[\w.\-/ ]+?)(?:\s+type="(?P<type>[^"]+)")?[^\]]*\]\s*$')
_YAML_LINE = re.compile(r"^(?P<lead>\s*(?:-\s+)*(?:[\w.\- ]+:\s+)?)(?P<value>\S.*?)\s*$")
_YAML_BLOCK = re.compile(r"^[|>][+-]?\d*\s*(?:#.*)?$")
_YAML_QUOTED = re.compile(r'^"(?P<inner>(?:[^"\\]|\\.)*)"(?P<tail>\s*(?:#.*)?)$|^\'(?P<single>(?:[^\']|\'\')*)\'(?P<stail>\s*(?:#.*)?)$')
_YAML_PLAIN_COMMENT = re.compile(r"\s+#.*$")
_YAML_UNSAFE_START = tuple("-?:,[]{}#&*!|>'\"%@`")
# Machine-written or plugin configuration, never text the player reads.
_CODE_PROPS = frozenset({"code", "source_code", "script", "shader_code", "resource_path", "resource_name"})


@dataclass(slots=True)
class _Hit:
    """One translatable span inside a physical line (or merged multi-line value)."""

    key: str
    text: str
    line: int
    context: str
    start: int
    end: int
    encode: Callable[[str], str]


Chunk = tuple[str, list[_Hit]]


def unescape_godot(raw: str) -> str:
    """Resolves the escapes of a Godot string literal (``\\n``, ``\\"``, ``\\uXXXX`` ...)."""

    def _one(match: re.Match[str]) -> str:
        token = match.group(1)
        if token.startswith("u") and len(token) == 5:
            return chr(int(token[1:], 16))
        return _ESCAPES.get(token, match.group(0))

    return _GODOT_ESCAPE.sub(_one, raw)


def escape_godot(text: str) -> str:
    """Inverse of :func:`unescape_godot` for the characters that cannot sit raw inside a literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


def _split_eol(raw: str) -> tuple[str, str]:
    body = raw.rstrip("\r\n")
    return body, raw[len(body):]


def _quoted(text: str) -> str:
    return f'"{escape_godot(text)}"'


# --- .txt: one sentence per line -------------------------------------------------------------------------------


def _scan_txt(content: str, source_file: str) -> Iterator[Chunk]:
    for line_no, raw in enumerate(content.splitlines(keepends=True), start=1):
        body, _eol = _split_eol(raw)
        text = body.strip()
        if not looks_like_sentence(text):
            yield raw, []
            continue
        start = len(body) - len(body.lstrip())
        hit = _Hit(f"{source_file}::L{line_no}", text, line_no, f"Line {line_no}", start, start + len(text),
                   lambda t: t.replace("\r", " ").replace("\n", " "))
        yield raw, [hit]


# --- YAML ------------------------------------------------------------------------------------------------------


def _yaml_plain_encode(text: str) -> str:
    unsafe = text.startswith(_YAML_UNSAFE_START) or ": " in text or " #" in text or "\n" in text or text != text.strip()
    return _quoted(text) if unsafe else text


def _yaml_value(value: str) -> tuple[str, int, int, Callable[[str], str]] | None:
    """(text, offset, length, encoder) of a scalar value; None for flow collections, anchors, tags and aliases."""
    quoted = _YAML_QUOTED.match(value)
    if quoted and quoted.group("inner") is not None:
        end = len(value) - len(quoted.group("tail"))
        return unescape_godot(quoted.group("inner")), 0, end, _quoted
    if quoted:
        end = len(value) - len(quoted.group("stail"))
        return quoted.group("single").replace("''", "'"), 0, end, lambda t: "'" + t.replace("'", "''").replace("\n", " ") + "'"
    if value.startswith(("{", "[", "&", "*", "!", "|", ">", "#", '"', "'")):
        return None
    plain = _YAML_PLAIN_COMMENT.sub("", value)
    return (plain, 0, len(plain), _yaml_plain_encode) if not plain.endswith(":") else None


def _yaml_block_line(body: str, line_no: int, source_file: str) -> list[_Hit]:
    text = body.strip()
    if not looks_like_sentence(text):
        return []
    start = len(body) - len(body.lstrip())
    return [_Hit(f"{source_file}::L{line_no}", text, line_no, f"Line {line_no}", start, start + len(text),
                 lambda t: t.replace("\r", " ").replace("\n", " "))]


def _scan_yaml(content: str, source_file: str) -> Iterator[Chunk]:
    block_indent: int | None = None
    for line_no, raw in enumerate(content.splitlines(keepends=True), start=1):
        body, _eol = _split_eol(raw)
        indent = len(body) - len(body.lstrip())
        if block_indent is not None and (not body.strip() or indent > block_indent):
            yield raw, _yaml_block_line(body, line_no, source_file)
            continue
        block_indent = None
        if not body.strip() or body.lstrip().startswith(("#", "---", "...")):
            yield raw, []
            continue
        match = _YAML_LINE.match(body)
        value = match.group("value") if match else ""
        if _YAML_BLOCK.match(value):
            block_indent = indent
        parsed = _yaml_value(value) if match else None
        if not match or parsed is None or not looks_like_sentence(parsed[0]):
            yield raw, []
            continue
        text, offset, length, encode = parsed
        start = match.start("value") + offset
        yield raw, [_Hit(f"{source_file}::L{line_no}", text, line_no, body.strip()[:60], start, start + length, encode)]


# --- Godot text variants: .tres and .cfg / .ini -----------------------------------------------------------------


def _assignment_hits(body: str, line_no: int, source_file: str, section: str, allow_bare: bool) -> list[_Hit]:
    match = _ASSIGNMENT.match(body)
    if not match or match.group("prop") in _CODE_PROPS:
        return []
    prop, value = match.group("prop"), match.group("value")
    offset = match.start("value")
    literals = list(_GODOT_STRING.finditer(value))
    hits: list[_Hit] = []
    for index, literal in enumerate(literals):
        text = unescape_godot(literal.group(1))
        known = len(literals) == 1 and value.strip() == literal.group(0) and SceneHarvester.is_text_property(prop)
        if not (known and text.strip()) and not looks_like_sentence(text):
            continue
        suffix = "" if len(literals) == 1 else f"#{index}"
        hits.append(_Hit(f"{source_file}::L{line_no}::{prop}{suffix}", text, line_no, f"{section}.{prop}".strip("."),
                         offset + literal.start(), offset + literal.end(), _quoted))
    if not literals and allow_bare and looks_like_sentence(value) and not value.lstrip().startswith(("[", "{")):
        stripped = value.strip()
        start = offset + value.index(stripped)
        hits.append(_Hit(f"{source_file}::L{line_no}::{prop}", stripped, line_no, f"{section}.{prop}".strip("."),
                         start, start + len(stripped), lambda t: t.replace("\n", " ")))
    return hits


def _scan_assignments(content: str, source_file: str, allow_bare: bool) -> Iterator[Chunk]:
    section = ""
    for line_no, raw in SceneHarvester._logical_lines(content):
        body, _eol = _split_eol(raw)
        header = _SECTION.match(body.strip())
        if header:
            section = header.group("type") or header.group("name")
            yield raw, []
        elif body.lstrip().startswith((";", "#")):
            yield raw, []
        else:
            yield raw, _assignment_hits(body, line_no, source_file, section, allow_bare)


# --- public API ------------------------------------------------------------------------------------------------


def _chunks(kind: str, content: str, source_file: str) -> Iterator[Chunk]:
    if kind == KIND_TXT:
        return _scan_txt(content, source_file)
    if kind == KIND_YAML:
        return _scan_yaml(content, source_file)
    return _scan_assignments(content, source_file, allow_bare=kind == KIND_CFG)


def harvest_data_file(kind: str, content: str, source_file: str) -> list[HarvestedEntry]:
    """Translatable sentences of a ``.txt`` / YAML / ``.cfg`` / ``.tres`` file."""
    source_type = StringSourceType.NARRATIVE if kind in (KIND_TXT, KIND_YAML) else StringSourceType.SCENE_UI
    return [
        HarvestedEntry(
            key=hit.key,
            source_text=hit.text,
            source_file=source_file,
            source_type=source_type,
            context=hit.context,
            line_number=hit.line,
            metadata={"format": kind},
        )
        for _raw, hits in _chunks(kind, content, source_file)
        for hit in hits
    ]


def apply_data_translations(kind: str, content: str, translations: dict[str, str], source_file: str) -> str:
    """Writes translated text back over the harvested spans, leaving every other byte as it was."""
    out: list[str] = []
    for raw, hits in _chunks(kind, content, source_file):
        body, eol = _split_eol(raw)
        for hit in sorted(hits, key=lambda h: h.start, reverse=True):
            translated = translations.get(hit.key)
            if translated:
                body = body[: hit.start] + hit.encode(translated) + body[hit.end:]
        out.append(body + eol)
    return "".join(out)
