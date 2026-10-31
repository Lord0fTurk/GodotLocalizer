"""Reading and rebuilding Godot ``OptimizedTranslation`` tables (the default ``.translation`` output).

An optimized table stores no source keys, only perfect-hash slots:

    hash_table   int32[]  slot -> offset into bucket_table, or -1 (0xFFFFFFFF) when empty
    bucket_table int32[]  per bucket: size, hash seed, then ``size`` x (key_hash, str_offset, comp, uncomp)
    strings      bytes    concatenated values; ``comp == uncomp`` means stored raw, otherwise smaz

Because lookups use only hashes, a table with the same slots and new values translates the game
without ever knowing its keys.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from src.core.binary.smaz import SmazError, decompress

_EMPTY_SLOT = -1
_ELEM_INTS = 4


@dataclass(slots=True)
class OptimizedElement:
    """One translated value of the table (index = its position in iteration order)."""

    bucket_offset: int
    key_hash: int
    text: str


class OptimizedTranslationError(ValueError):
    """The arrays do not form a valid optimized translation."""


def _as_int32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value >= 0x80000000 else value


def _bucket_offsets(hash_table: list[int]) -> list[int]:
    return sorted({_as_int32(v) for v in hash_table if _as_int32(v) != _EMPTY_SLOT})


def _decode(strings: bytes, offset: int, comp: int, uncomp: int) -> bytes:
    chunk = strings[offset:offset + comp]
    if len(chunk) != comp:
        raise OptimizedTranslationError("String range exceeds the strings blob.")
    if comp == uncomp:
        return chunk
    try:
        expanded = decompress(chunk)
    except SmazError as exc:
        raise OptimizedTranslationError(str(exc)) from exc
    if len(expanded) != uncomp:
        raise OptimizedTranslationError("Decompressed length does not match the table.")
    return expanded


def read_elements(hash_table: list[int], bucket_table: list[int], strings: bytes) -> list[OptimizedElement]:
    """Decodes every value in the table, in bucket order."""
    elements: list[OptimizedElement] = []
    for start in _bucket_offsets(hash_table):
        if start < 0 or start + 2 > len(bucket_table):
            raise OptimizedTranslationError("Bucket offset out of range.")
        size = bucket_table[start]
        base = start + 2
        if size < 0 or base + size * _ELEM_INTS > len(bucket_table):
            raise OptimizedTranslationError("Bucket size out of range.")
        for i in range(size):
            key, offset, comp, uncomp = bucket_table[base + i * _ELEM_INTS: base + (i + 1) * _ELEM_INTS]
            raw = _decode(strings, offset, comp, uncomp)
            elements.append(OptimizedElement(start, key, raw.rstrip(b"\x00").decode("utf-8", errors="replace")))
    return elements


def rebuild(
    hash_table: list[int],
    bucket_table: list[int],
    strings: bytes,
    translations: dict[str, str],
) -> tuple[list[int], bytes]:
    """New (bucket_table, strings) where values found in ``translations`` are replaced.

    The hash table and all hashes are untouched; replaced and original values are both written raw
    (``comp == uncomp``), which the engine reads without decompression.
    """
    new_buckets = list(bucket_table)
    blob = bytearray()
    for start in _bucket_offsets(hash_table):
        size = bucket_table[start]
        base = start + 2
        for i in range(size):
            slot = base + i * _ELEM_INTS
            _key, offset, comp, uncomp = bucket_table[slot: slot + _ELEM_INTS]
            original = _decode(strings, offset, comp, uncomp)
            text = original.rstrip(b"\x00").decode("utf-8", errors="replace")
            payload = translations.get(text.strip(), "").encode("utf-8") or original
            payload = payload or b"\x00"
            new_buckets[slot + 1] = len(blob)
            new_buckets[slot + 2] = len(payload)
            new_buckets[slot + 3] = len(payload)
            blob += payload
    return new_buckets, bytes(blob)


def to_runtime_payload(hash_table: list[int], bucket_table: list[int], strings: bytes, locale: str) -> dict[str, object]:
    """JSON-friendly dict the bootstrap turns back into an ``OptimizedTranslation``."""
    return {
        "locale": locale,
        "hash_table": [_as_int32(v) for v in hash_table],
        "bucket_table": [_as_int32(v) for v in bucket_table],
        "strings": base64.b64encode(strings).decode("ascii"),
    }
