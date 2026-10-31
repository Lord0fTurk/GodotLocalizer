"""Reader for Godot's binary resource container (``RSRC``): ``.scn``, ``.res``, ``.translation``.

Layout (core/io/resource_format_binary.cpp), integers use the file's endianness:

    "RSRC" | endian u32 | use_real64 u32 | ver_major u32 | ver_minor u32 | ver_format u32
    type str | importmd_ofs u64 |
      Godot 4: flags u32 | [uid u64] | [script_class str] | 11 reserved u32
      Godot 3: 14 reserved u32
    string_table | external_resources | internal_resources(path, offset) | bodies

Strings are ``u32 length`` (including the trailing NUL) followed by UTF-8 bytes.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Any

MAGIC = b"RSRC"
COMPRESSED_MAGIC = b"RSCC"

FLAG_NAMED_SCENE_IDS = 1
FLAG_UIDS = 2
FLAG_REAL_T_IS_DOUBLE = 4
FLAG_HAS_SCRIPT_CLASS = 8
RESERVED_FIELDS_G4 = 11
RESERVED_FIELDS_G3 = 14

_MAX_DEPTH = 64
_MAX_DECOMPRESSED = 512 * 1024 * 1024

# Compression::Mode values used by FileAccessCompressed
_MODE_DEFLATE = 1
_MODE_ZSTD = 2
_MODE_GZIP = 3


class RSRCError(ValueError):
    """The data is not a binary resource this reader understands."""


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """Reference from a property to another resource (internal index or external path)."""

    kind: str  # "internal" | "external"
    index: int = -1
    path: str = ""


@dataclass(slots=True)
class Resource:
    """One resource body: its class name, path ("local://1" for sub-resources) and properties."""

    path: str
    type: str
    properties: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(slots=True)
class RSRCFile:
    """Parsed container; ``main`` is the last internal resource, i.e. the file's own resource."""

    type: str
    ver_major: int
    ver_format: int
    resources: list[Resource]
    external: list[tuple[str, str]]

    @property
    def main(self) -> Resource:
        return self.resources[-1]


def zstd_decompress(chunk: bytes, max_size: int) -> bytes:
    try:
        from compression import zstd  # Python 3.14+

        return zstd.decompress(chunk)
    except ImportError:
        pass
    try:
        import zstandard
    except ImportError as exc:
        raise RSRCError("Zstandard-compressed resource: install the 'zstandard' package.") from exc
    return zstandard.ZstdDecompressor().decompress(chunk, max_output_size=max_size)


def _decompress_block(chunk: bytes, mode: int, expected: int) -> bytes:
    try:
        if mode == _MODE_ZSTD:
            return zstd_decompress(chunk, expected)
        if mode == _MODE_DEFLATE:
            return zlib.decompress(chunk)
        if mode == _MODE_GZIP:
            return zlib.decompress(chunk, 31)
    except (zlib.error, ValueError, RuntimeError, OSError) as exc:
        raise RSRCError(f"Corrupt compressed block: {exc}") from exc
    raise RSRCError(f"Unsupported resource compression mode {mode}.")


def decompress_rscc(data: bytes) -> bytes:
    """Expands a ``RSCC`` container (Godot's FileAccessCompressed) into the plain resource stream.

    Layout after the magic: mode u32 | block_size u32 | total u32 | csize u32 x (total // block_size + 1)
    | blocks. Every block holds ``block_size`` bytes except the last (``total % block_size``).
    """
    if len(data) < 16:
        raise RSRCError("Truncated compressed resource.")
    mode, block_size, total = struct.unpack_from("<III", data, 4)
    if block_size == 0 or total > _MAX_DECOMPRESSED:
        raise RSRCError("Invalid compressed resource header.")
    count = total // block_size + 1
    table_end = 16 + count * 4
    if table_end > len(data):
        raise RSRCError("Truncated compressed resource.")
    sizes = struct.unpack_from(f"<{count}I", data, 16)
    out = bytearray()
    pos = table_end
    for index, csize in enumerate(sizes):
        expected = block_size if index < count - 1 else total % block_size
        if expected == 0:
            pos += csize
            continue
        chunk = data[pos:pos + csize]
        if len(chunk) != csize:
            raise RSRCError("Truncated compressed resource.")
        block = _decompress_block(chunk, mode, expected)
        if len(block) != expected:
            raise RSRCError("Compressed block has an unexpected size.")
        out += block
        pos += csize
    return bytes(out)


class _Cursor:
    def __init__(self, data: bytes, endian: str = "<") -> None:
        self.data = data
        self.pos = 0
        self.endian = endian
        self.real_size = 4

    def unpack(self, fmt: str) -> tuple[Any, ...]:
        full = self.endian + fmt
        size = struct.calcsize(full)
        if self.pos + size > len(self.data):
            raise RSRCError("Unexpected end of data.")
        values = struct.unpack_from(full, self.data, self.pos)
        self.pos += size
        return values

    def u32(self) -> int:
        return int(self.unpack("I")[0])

    def u64(self) -> int:
        return int(self.unpack("Q")[0])

    def take(self, count: int) -> bytes:
        if count < 0 or self.pos + count > len(self.data):
            raise RSRCError("Unexpected end of data.")
        chunk = self.data[self.pos:self.pos + count]
        self.pos += count
        return chunk

    def skip(self, count: int) -> None:
        self.take(count)

    def string(self) -> str:
        length = self.u32()
        return self.take(length).rstrip(b"\x00").decode("utf-8", errors="replace")

    def reals(self, count: int) -> tuple[float, ...]:
        return self.unpack(f"{count}{'d' if self.real_size == 8 else 'f'}")

    def count(self, element_size: int = 1) -> int:
        """Reads a container length and rejects values that cannot fit in the remaining data."""
        length = self.u32() & 0x7FFFFFFF
        if length * max(element_size, 1) > len(self.data) - self.pos:
            raise RSRCError("Container length exceeds file size.")
        return length


class _Parser:
    def __init__(self, cur: _Cursor) -> None:
        self.cur = cur
        self.names: list[str] = []
        self.external: list[tuple[str, str]] = []

    def name_ref(self) -> str:
        ident = self.cur.u32()
        if ident & 0x80000000:
            return self.cur.take(ident & 0x7FFFFFFF).rstrip(b"\x00").decode("utf-8", errors="replace")
        if ident >= len(self.names):
            raise RSRCError("String table index out of range.")
        return self.names[ident]

    def variant(self, depth: int = 0) -> Any:
        if depth > _MAX_DEPTH:
            raise RSRCError("Variant nesting too deep.")
        cur = self.cur
        tid = cur.u32()
        simple = self._simple(tid)
        if simple is not _UNSET:
            return simple
        return self._container(tid, depth)

    def _simple(self, tid: int) -> Any:
        cur = self.cur
        if tid == 1:
            return None
        if tid == 2:
            return bool(cur.u32())
        if tid == 3:
            return int(cur.unpack("i")[0])
        if tid == 40:
            return int(cur.unpack("q")[0])
        if tid == 4:
            return float(cur.reals(1)[0])
        if tid == 41:
            return float(cur.unpack("d")[0])
        if tid in (5, 44):
            return cur.string()
        if tid == 20:
            return cur.unpack("4f")
        if tid == 23:
            return cur.u32()
        if tid == 22:
            return self._node_path()
        if tid in _REAL_VECTORS:
            return cur.reals(_REAL_VECTORS[tid])
        if tid in _INT_VECTORS:
            return cur.unpack(f"{_INT_VECTORS[tid]}i")
        if tid == 24:
            return self._object()
        if tid in (42, 43, 25, 21):
            raise RSRCError(f"Unsupported variant type {tid}.")
        return _UNSET

    def _container(self, tid: int, depth: int) -> Any:
        cur = self.cur
        if tid == 26:
            length = cur.count(8)
            return {self._hashable(self.variant(depth + 1)): self.variant(depth + 1) for _ in range(length)}
        if tid == 30:
            return [self.variant(depth + 1) for _ in range(cur.count(4))]
        if tid == 31:
            length = cur.count()
            data = cur.take(length)
            cur.skip(-length % 4)
            return data
        if tid == 34:
            return [cur.string() for _ in range(cur.count(4))]
        if tid in _PACKED_NUMERIC:
            fmt, size = _PACKED_NUMERIC[tid]
            length = cur.count(size)
            return list(cur.unpack(f"{length}{fmt}")) if length else []
        if tid in _PACKED_REALS:
            width = _PACKED_REALS[tid]
            length = cur.count(width * cur.real_size)
            return [cur.reals(width) for _ in range(length)]
        if tid == 36:
            return [cur.unpack("4f") for _ in range(cur.count(16))]
        raise RSRCError(f"Unknown variant type {tid}.")

    @staticmethod
    def _hashable(value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    def _node_path(self) -> str:
        cur = self.cur
        name_count, raw_sub = cur.unpack("HH")
        absolute = bool(raw_sub & 0x8000)
        names = [self.name_ref() for _ in range(name_count)]
        subs = [self.name_ref() for _ in range(raw_sub & 0x7FFF)]
        path = "/".join(names)
        if absolute:
            path = "/" + path
        return path + (":" + ":".join(subs) if subs else "")

    def _object(self) -> ObjectRef | None:
        cur = self.cur
        kind = cur.u32()
        if kind == 0:
            return None
        if kind == 2:
            return ObjectRef("internal", index=cur.u32())
        if kind == 3:
            index = cur.u32()
            path = self.external[index][1] if index < len(self.external) else ""
            return ObjectRef("external", index=index, path=path)
        if kind == 1:
            cur.string()
            return ObjectRef("external", path=cur.string())
        raise RSRCError(f"Unknown object kind {kind}.")


_UNSET = object()
_REAL_VECTORS = {10: 2, 11: 4, 12: 3, 13: 4, 14: 4, 15: 6, 16: 9, 17: 12, 18: 6, 50: 4, 52: 16}
_INT_VECTORS = {45: 2, 46: 4, 47: 3, 51: 4}
_PACKED_NUMERIC = {32: ("i", 4), 33: ("f", 4), 48: ("q", 8), 49: ("d", 8)}
_PACKED_REALS = {35: 3, 37: 2, 53: 4}


def _read_header(cur: _Cursor) -> tuple[str, int, int, int]:
    big_endian, use_real64 = cur.unpack("II")
    if big_endian:
        cur.endian = ">"
    ver_major, _ver_minor, ver_format = cur.unpack("III")
    if ver_major not in (3, 4):
        raise RSRCError(f"Unsupported Godot major version {ver_major}.")
    type_name = cur.string()
    cur.u64()  # import metadata offset
    if ver_major >= 4:
        flags = cur.u32()
        if flags & FLAG_UIDS:
            cur.u64()
        if flags & FLAG_HAS_SCRIPT_CLASS:
            cur.string()
        cur.real_size = 8 if flags & FLAG_REAL_T_IS_DOUBLE else 4
        cur.skip(RESERVED_FIELDS_G4 * 4)
    else:
        cur.real_size = 8 if use_real64 else 4
        cur.skip(RESERVED_FIELDS_G3 * 4)
    return type_name, ver_major, ver_format, flags if ver_major >= 4 else 0


def parse_rsrc(data: bytes) -> RSRCFile:
    """Parses a binary resource; individual unreadable resources keep their ``error`` and empty properties."""
    if data[:4] == COMPRESSED_MAGIC:
        # The decompressed stream has no magic, and the offsets stored inside it start at 0.
        cur = _Cursor(decompress_rscc(data))
    elif data[:4] == MAGIC:
        cur = _Cursor(data)
        cur.skip(4)
    else:
        raise RSRCError("Not a binary resource (missing RSRC/RSCC magic).")
    type_name, ver_major, ver_format, flags = _read_header(cur)
    parser = _Parser(cur)

    parser.names = [cur.string() for _ in range(cur.count(4))]
    for _ in range(cur.count(8)):
        ext_type, ext_path = cur.string(), cur.string()
        if flags & FLAG_UIDS:
            cur.u64()
        parser.external.append((ext_type, ext_path))
    table = [(cur.string(), cur.u64()) for _ in range(cur.count(12))]
    if not table:
        raise RSRCError("Resource has no internal resources.")

    resources: list[Resource] = []
    for path, offset in table:
        res = Resource(path=path, type="")
        try:
            cur.pos = offset
            res.type = cur.string()
            for _ in range(cur.u32()):
                key = parser.name_ref()
                res.properties[key] = parser.variant()
        except RSRCError as exc:
            res.error = str(exc)
        resources.append(res)
    return RSRCFile(type_name, ver_major, ver_format, resources, parser.external)
