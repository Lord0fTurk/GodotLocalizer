"""Native string-constant extractor for compiled GDScript (``.gdc``, Godot 4.3+ tokenizer buffers).

A ``GDSC`` file is a tokenised script, not a program image: every identifier and every literal sits in a
table in front of the token stream. The table layout is stable across the 4.3+ releases, so all string
literals can be read without decompiling anything (modules/gdscript/gdscript_tokenizer_buffer.cpp):

    "GDSC" | version u32 | decompressed_size u32 (0 = payload is raw) | payload (zstd when compressed)
    payload: identifier_count, constant_count, line_count, token_count (u32 each) |
             identifiers: len u32, len x u32 code points XOR 0xB6 |
             constants: Godot Variant binary encoding (core/io/marshalls.cpp)

Older bytecode (4.0-4.2) uses a different layout and is left to the optional GDRE Tools backend.
Because the token stream is not interpreted, no call context is known: callers filter the strings by shape.
"""

from __future__ import annotations

import struct

from src.core.binary.rsrc import RSRCError, zstd_decompress

MAGIC = b"GDSC"
_XOR = 0xB6
_MIN_VERSION, _MAX_VERSION = 100, 140  # tokenizer buffer versions of Godot 4.3 and later
_MAX_PAYLOAD = 64 * 1024 * 1024

# Marshalls variant type ids (Variant::Type) and how a value is laid out after the 4-byte header.
_T_NIL, _T_BOOL, _T_INT, _T_FLOAT, _T_STRING = 0, 1, 2, 3, 4
_T_STRING_NAME = 21
_T_NODE_PATH = 22
_FLAG_64 = 1 << 16
_REAL_VECTOR_COMPONENTS = {5: 2, 7: 4, 9: 3, 10: 4, 11: 4, 12: 6, 13: 9, 14: 12, 15: 4, 16: 6, 36: 4, 37: 16}
_INT_VECTOR_COMPONENTS = {6: 2, 8: 4, 17: 3, 18: 4}


class GdcError(ValueError):
    """The buffer is not a compiled script this reader understands."""


def _decode_constant(buf: bytes, pos: int) -> tuple[object, int]:
    """Decodes one Variant at ``pos``; returns (python value or None, new position)."""
    if pos + 4 > len(buf):
        raise GdcError("Truncated constant table.")
    header = struct.unpack_from("<I", buf, pos)[0]
    kind, wide = header & 0xFFFF, bool(header & _FLAG_64)
    pos += 4
    if kind == _T_NIL:
        return None, pos
    if kind == _T_BOOL:
        return bool(struct.unpack_from("<I", buf, pos)[0]), pos + 4
    if kind == _T_INT:
        size = 8 if wide else 4
        return struct.unpack_from("<q" if wide else "<i", buf, pos)[0], pos + size
    if kind == _T_FLOAT:
        size = 8 if wide else 4
        return struct.unpack_from("<d" if wide else "<f", buf, pos)[0], pos + size
    if kind in (_T_STRING, _T_STRING_NAME):
        length = struct.unpack_from("<I", buf, pos)[0]
        start = pos + 4
        if start + length > len(buf):
            raise GdcError("String constant exceeds the buffer.")
        text = buf[start:start + length].decode("utf-8", errors="replace")
        return text, start + length + (-length % 4)
    if kind == _T_NODE_PATH:
        return None, _skip_node_path(buf, pos)
    if kind in _REAL_VECTOR_COMPONENTS:
        return None, pos + _REAL_VECTOR_COMPONENTS[kind] * (8 if wide else 4)
    if kind in _INT_VECTOR_COMPONENTS:
        return None, pos + _INT_VECTOR_COMPONENTS[kind] * (8 if wide else 4)
    raise GdcError(f"Unsupported constant type {kind}.")


def _skip_node_path(buf: bytes, pos: int) -> int:
    """Steps over an encoded NodePath (new format: name count with the high bit set, subname count, flags)."""
    if pos + 4 > len(buf):
        raise GdcError("Truncated constant table.")
    names = struct.unpack_from("<I", buf, pos)[0]
    if not names & 0x80000000:
        return pos + 4 + names + (-names % 4)  # legacy format: one plain string
    if pos + 12 > len(buf):
        raise GdcError("Truncated constant table.")
    total = (names & 0x7FFFFFFF) + struct.unpack_from("<I", buf, pos + 4)[0]
    pos += 12
    for _ in range(total):
        if pos + 4 > len(buf):
            raise GdcError("Truncated constant table.")
        length = struct.unpack_from("<I", buf, pos)[0]
        pos += 4 + length + (-length % 4)
    return pos


def is_compiled_script(data: bytes) -> bool:
    """True for a Godot 4.3+ tokenizer buffer."""
    if len(data) < 12 or data[:4] != MAGIC:
        return False
    return _MIN_VERSION <= struct.unpack_from("<I", data, 4)[0] <= _MAX_VERSION


def extract_string_constants(data: bytes) -> list[str]:
    """All string literals of a compiled script, in declaration order (duplicates removed)."""
    if not is_compiled_script(data):
        raise GdcError("Not a Godot 4.3+ compiled script.")
    decompressed_size = struct.unpack_from("<I", data, 8)[0]
    payload = data[12:]
    if decompressed_size:
        if decompressed_size > _MAX_PAYLOAD:
            raise GdcError("Compiled script is unreasonably large.")
        try:
            payload = zstd_decompress(payload, decompressed_size)
        except RSRCError as exc:
            raise GdcError(str(exc)) from exc
        if len(payload) != decompressed_size:
            raise GdcError("Decompressed size does not match the header.")
    if len(payload) < 16:
        raise GdcError("Truncated compiled script.")

    identifier_count, constant_count = struct.unpack_from("<II", payload, 0)
    pos = 16
    for _ in range(identifier_count):
        if pos + 4 > len(payload):
            raise GdcError("Truncated identifier table.")
        pos += 4 + struct.unpack_from("<I", payload, pos)[0] * 4
    if pos > len(payload) or constant_count > len(payload):
        raise GdcError("Identifier table exceeds the buffer.")

    strings: dict[str, None] = {}
    for _ in range(constant_count):
        value, pos = _decode_constant(payload, pos)
        if isinstance(value, str):
            strings.setdefault(value)
    return list(strings)
