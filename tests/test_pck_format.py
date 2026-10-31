"""Byte-level PCK format tests built from the Godot spec (core/io/file_access_pack.{h,cpp}).

The fixtures here are assembled with raw ``struct`` calls, independent of ``PCKWriter``, so a
shared mistake in reader and writer cannot make these tests pass by accident.
"""

import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from src.core.pck.constants import (
    PACK_FILE_REMOVAL,
    PACK_FLAG_DIR_ENCRYPTED,
    PACK_FLAG_REL_FILEBASE,
    PCK_FORMAT_VERSION_1,
    PCK_FORMAT_VERSION_2,
    PCK_FORMAT_VERSION_3,
    PCK_MAGIC,
)
from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter

# Values copied from Godot's PackFlags / PackFileFlags enums.
GODOT_PACK_DIR_ENCRYPTED = 1 << 0
GODOT_PACK_REL_FILEBASE = 1 << 1
GODOT_PACK_FILE_REMOVAL = 1 << 1

PAYLOAD_A = b"hello godot"
PAYLOAD_B = b"second file payload"


def _dir_entry(path: str, ofs: int, data: bytes, flags: int | None) -> bytes:
    raw = path.encode()
    padded = raw + b"\x00" * (-len(raw) % 4)
    # Godot stores the *padded* length and reads exactly that many bytes.
    out = struct.pack("<I", len(padded)) + padded + struct.pack("<QQ", ofs, len(data)) + hashlib.md5(data).digest()
    return out + (struct.pack("<I", flags) if flags is not None else b"")


def _build(version: int, pack_flags: int, files: list[tuple[str, bytes, int]], prefix: bytes = b"") -> bytes:
    """Assemble a PCK by hand. ``prefix`` simulates an executable the pack is appended to."""
    header_len = 4 + 4 + 12 + (0 if version == 1 else 4 + 8) + (8 if version >= 3 else 0)
    header_len += (16 if version < 3 else 14) * 4
    has_flags = version >= 2
    dir_len = 4 + sum(len(_dir_entry(p, 0, d, 0 if has_flags else None)) for p, d, _ in files)
    file_base = header_len + dir_len
    body = b""
    entries = b""
    for path, data, flags in files:
        ofs = len(body) if has_flags else file_base + len(body)
        entries += _dir_entry(path, ofs, data, flags if has_flags else None)
        body += data
    head = PCK_MAGIC + struct.pack("<I", version) + struct.pack("<III", 4, 3, 0)
    if version >= 2:
        head += struct.pack("<I", pack_flags) + struct.pack("<Q", file_base)
    if version >= 3:
        head += struct.pack("<Q", header_len)  # dir_offset, relative to pack start
    head += b"\x00" * ((16 if version < 3 else 14) * 4)
    assert len(head) == header_len
    return prefix + head + struct.pack("<I", len(files)) + entries + body


class TestReaderAgainstGodotSpec(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _open(self, blob: bytes) -> PCKReader:
        path = self.dir / "game.pck"
        path.write_bytes(blob)
        reader = PCKReader(path)
        reader.open()
        return reader

    def test_constants_match_godot_enums(self) -> None:
        self.assertEqual(PACK_FLAG_DIR_ENCRYPTED, GODOT_PACK_DIR_ENCRYPTED)
        self.assertEqual(PACK_FLAG_REL_FILEBASE, GODOT_PACK_REL_FILEBASE)
        self.assertEqual(PACK_FILE_REMOVAL, GODOT_PACK_FILE_REMOVAL)

    def test_v1_godot3(self) -> None:
        reader = self._open(_build(1, 0, [("res://a.txt", PAYLOAD_A, 0)]))
        self.assertEqual(reader.read_file_bytes("res://a.txt"), PAYLOAD_A)

    def test_v2_without_rel_filebase(self) -> None:
        reader = self._open(_build(2, 0, [("res://a.txt", PAYLOAD_A, 0), ("res://b.txt", PAYLOAD_B, 0)]))
        self.assertEqual(reader.read_file_bytes("res://b.txt"), PAYLOAD_B)

    def test_v2_with_rel_filebase_has_no_directory_offset(self) -> None:
        reader = self._open(_build(2, GODOT_PACK_REL_FILEBASE, [("res://a.txt", PAYLOAD_A, 0)]))
        self.assertEqual(reader.directory_offset, 0)
        self.assertEqual(reader.read_file_bytes("res://a.txt"), PAYLOAD_A)

    def test_v3_uses_directory_offset(self) -> None:
        reader = self._open(_build(3, GODOT_PACK_REL_FILEBASE, [("res://a.txt", PAYLOAD_A, 0)]))
        self.assertGreater(reader.directory_offset, 0)
        self.assertEqual(reader.read_file_bytes("res://a.txt"), PAYLOAD_A)

    def test_embedded_v2_rel_filebase_offsets_are_pack_relative(self) -> None:
        pck = _build(2, GODOT_PACK_REL_FILEBASE, [("res://a.txt", PAYLOAD_A, 0)])
        exe = b"MZ" + b"\x90" * 1000 + pck + struct.pack("<Q", len(pck)) + PCK_MAGIC
        reader = self._open(exe)
        self.assertTrue(reader.is_embedded)
        self.assertEqual(reader.read_file_bytes("res://a.txt"), PAYLOAD_A)

    def test_encrypted_directory_is_rejected_not_misparsed(self) -> None:
        with self.assertRaisesRegex(ValueError, "encrypted"):
            self._open(_build(2, GODOT_PACK_DIR_ENCRYPTED, [("res://a.txt", PAYLOAD_A, 0)]))

    def test_removal_flag_drops_entry(self) -> None:
        reader = self._open(
            _build(3, GODOT_PACK_REL_FILEBASE, [
                ("res://a.txt", PAYLOAD_A, 0),
                ("res://a.txt", b"", GODOT_PACK_FILE_REMOVAL),
                ("res://b.txt", PAYLOAD_B, 0),
            ])
        )
        self.assertNotIn("res://a.txt", reader.entries)
        self.assertEqual(reader.read_file_bytes("res://b.txt"), PAYLOAD_B)


class TestWriterHeaderBytes(unittest.TestCase):
    def _write(self, version: int, engine: tuple[int, int, int]) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "t.pck"
            writer = PCKWriter(out, format_version=version, engine_version=engine)
            writer.add_bytes("res://a.txt", PAYLOAD_A)
            writer.build()
            return out.read_bytes()

    def test_v2_never_sets_dir_encrypted(self) -> None:
        blob = self._write(PCK_FORMAT_VERSION_2, (4, 2, 0))
        flags = struct.unpack_from("<I", blob, 20)[0]
        self.assertEqual(flags, GODOT_PACK_REL_FILEBASE)
        self.assertFalse(flags & GODOT_PACK_DIR_ENCRYPTED)

    def test_v3_flags_and_directory_offset(self) -> None:
        blob = self._write(PCK_FORMAT_VERSION_3, (4, 5, 0))
        self.assertEqual(struct.unpack_from("<I", blob, 4)[0], 3)
        self.assertEqual(struct.unpack_from("<I", blob, 20)[0], GODOT_PACK_REL_FILEBASE)
        file_base, dir_offset = struct.unpack_from("<QQ", blob, 24)
        self.assertEqual(struct.unpack_from("<I", blob, dir_offset)[0], 1)  # file_count at dir_offset
        self.assertGreaterEqual(file_base, dir_offset)
        self.assertEqual(blob[file_base:file_base + len(PAYLOAD_A)], PAYLOAD_A)

    def test_path_length_field_includes_alignment_padding(self) -> None:
        """Godot reads exactly the stored length; an unpadded one shifts every field that follows."""
        for version, engine in ((1, (3, 5, 0)), (2, (4, 2, 0)), (3, (4, 5, 0))):
            blob = self._write(version, engine)
            if version == 3:
                count_pos = struct.unpack_from("<Q", blob, 32)[0]
            else:
                count_pos = 4 + 4 + 12 + (0 if version == 1 else 12) + 64
            self.assertEqual(struct.unpack_from("<I", blob, count_pos)[0], 1)
            stored = struct.unpack_from("<I", blob, count_pos + 4)[0]
            self.assertEqual(stored % 4, 0, f"format {version}")
            self.assertEqual(blob[count_pos + 8:count_pos + 8 + len("res://a.txt")], b"res://a.txt")

    def test_v1_has_no_flags_field(self) -> None:
        blob = self._write(PCK_FORMAT_VERSION_1, (3, 5, 0))
        self.assertEqual(struct.unpack_from("<I", blob, 4)[0], 1)
        # 4 magic + 4 version + 12 engine + 64 reserved, then file_count
        self.assertEqual(struct.unpack_from("<I", blob, 84)[0], 1)


if __name__ == "__main__":
    unittest.main()
