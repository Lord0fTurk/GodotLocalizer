"""Reader for compiled gettext catalogs (``.mo``), the second format Godot's PO loader accepts.

Layout (GNU gettext, also parsed by ``core/io/translation_loader_po.cpp``):

    magic u32 (0x950412de, or byte-swapped) | revision u32 (major = high 16 bits, must be <= 1) |
    count N | offset of the original-strings table | offset of the translated-strings table | ...
    tables: N x (length u32, offset u32) of NUL-terminated strings

An original string is ``[msgctxt 0x04] msgid [0x00 msgid_plural]``, a translation is its ``msgstr`` forms
separated by 0x00. The entry with an empty original is the header (``Language``, ``Plural-Forms``).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

MAGIC = 0x950412DE
_MAX_MESSAGES = 2_000_000


class MoError(ValueError):
    """The data is not a compiled gettext catalog this reader understands."""


@dataclass(slots=True)
class MoMessage:
    context: str
    msgid: str
    msgid_plural: str
    msgstrs: list[str]


@dataclass(slots=True)
class MoCatalog:
    locale: str = ""
    plural_forms: str = ""
    messages: list[MoMessage] = field(default_factory=list)


def is_mo(data: bytes) -> bool:
    return len(data) >= 28 and MAGIC in (struct.unpack_from("<I", data)[0], struct.unpack_from(">I", data)[0])


def _header_value(header: str, name: str) -> str:
    match = re.search(rf"^{name}:\s*(.*?)\s*$", header, re.MULTILINE | re.IGNORECASE)
    return match.group(1) if match else ""


def _table(data: bytes, offset: int, count: int, endian: str) -> list[bytes]:
    if offset < 0 or offset + count * 8 > len(data):
        raise MoError("Truncated string table.")
    strings: list[bytes] = []
    for index in range(count):
        length, start = struct.unpack_from(endian + "II", data, offset + index * 8)
        if start + length > len(data):
            raise MoError("String outside the file.")
        strings.append(data[start:start + length])
    return strings


def parse_mo(data: bytes) -> MoCatalog:
    """Parses a ``.mo`` file; raises :class:`MoError` for anything else."""
    if len(data) < 28:
        raise MoError("Too short for a .mo file.")
    magic_le = struct.unpack_from("<I", data)[0]
    endian = "<" if magic_le == MAGIC else ">" if struct.unpack_from(">I", data)[0] == MAGIC else ""
    if not endian:
        raise MoError("Not a .mo file.")
    revision, count, originals_at, translations_at = struct.unpack_from(endian + "IIII", data, 4)
    if revision >> 16 > 1 or count > _MAX_MESSAGES:
        raise MoError("Unsupported .mo revision or size.")
    originals = _table(data, originals_at, count, endian)
    translations = _table(data, translations_at, count, endian)

    catalog = MoCatalog()
    for original, translated in zip(originals, translations):
        if not original:
            header = translated.decode("utf-8", errors="replace")
            catalog.locale = _header_value(header, "X-Language") or _header_value(header, "Language")
            catalog.plural_forms = _header_value(header, "Plural-Forms")
            continue
        context, _sep, rest = original.partition(b"\x04") if b"\x04" in original else (b"", b"", original)
        msgid, _nul, plural = rest.partition(b"\x00")
        catalog.messages.append(MoMessage(
            context.decode("utf-8", errors="replace"),
            msgid.decode("utf-8", errors="replace"),
            plural.decode("utf-8", errors="replace"),
            [part.decode("utf-8", errors="replace") for part in translated.split(b"\x00")],
        ))
    return catalog
