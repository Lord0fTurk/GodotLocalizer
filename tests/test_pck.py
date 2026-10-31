"""Unit tests for Godot PCK binary reader, writer, and embedded trailer detection."""

import struct
import tempfile
import unittest
from pathlib import Path

from src.core.pck.constants import (
    PCK_FORMAT_VERSION_1,
    PCK_FORMAT_VERSION_2,
    PCK_MAGIC,
)
from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter


class TestPCKBinaryEngine(unittest.TestCase):
    """Verifies PCK packing, unpacking, round-trip, and trailer resolution."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_godot4_pck_roundtrip(self) -> None:
        """Tests building and reading a Godot 4.x (Format 2) PCK."""
        pck_file = self.base_path / "game_v4.pck"
        writer = PCKWriter(
            output_path=pck_file,
            format_version=PCK_FORMAT_VERSION_2,
            engine_version=(4, 2, 2),
        )

        test_content_1 = b"Hello from Godot 4 Scene!"
        test_content_2 = b'{"dialogue": "Turkish Localization Test \xc5\x9f\xc4\x9f\xc4\xb1"}'

        writer.add_bytes("res://scenes/main.tscn", test_content_1)
        writer.add_bytes("res://data/dialogue.json", test_content_2)
        count = writer.build()
        self.assertEqual(count, 2)

        # Read back
        reader = PCKReader(pck_file)
        reader.open()

        self.assertFalse(reader.is_embedded)
        self.assertEqual(reader.format_version, PCK_FORMAT_VERSION_2)
        self.assertEqual(reader.engine_version, (4, 2, 2))
        self.assertIn("res://scenes/main.tscn", reader.entries)
        self.assertIn("res://data/dialogue.json", reader.entries)

        # Verify bytes
        extracted_1 = reader.read_file_bytes("res://scenes/main.tscn")
        self.assertEqual(extracted_1, test_content_1)

        # Test extraction to disk with MD5 verification
        extract_dir = self.base_path / "extracted_v4"
        extracted_count = reader.extract_all(extract_dir)
        self.assertEqual(extracted_count, 2)

        file_on_disk = extract_dir / "scenes" / "main.tscn"
        self.assertTrue(file_on_disk.exists())
        self.assertEqual(file_on_disk.read_bytes(), test_content_1)

    def test_godot3_pck_roundtrip(self) -> None:
        """Tests building and reading a Godot 3.x (Format 1) PCK."""
        pck_file = self.base_path / "game_v3.pck"
        writer = PCKWriter(
            output_path=pck_file,
            format_version=PCK_FORMAT_VERSION_1,
            engine_version=(3, 5, 3),
        )

        writer.add_bytes("res://scripts/player.gd", b"extends KinematicBody2D\n")
        writer.build()

        reader = PCKReader(pck_file)
        reader.open()

        self.assertFalse(reader.is_embedded)
        self.assertEqual(reader.format_version, PCK_FORMAT_VERSION_1)
        self.assertEqual(reader.engine_version, (3, 5, 3))
        self.assertIn("res://scripts/player.gd", reader.entries)
        self.assertEqual(
            reader.read_file_bytes("res://scripts/player.gd"),
            b"extends KinematicBody2D\n",
        )

    def test_embedded_exe_trailer_detection(self) -> None:
        """Simulates a Godot self-contained executable with embedded PCK and 12-byte trailer."""
        # 1. Build a valid PCK in memory/temp
        standalone_pck = self.base_path / "standalone.pck"
        writer = PCKWriter(
            output_path=standalone_pck,
            format_version=PCK_FORMAT_VERSION_2,
            engine_version=(4, 1, 0),
        )
        sample_data = b"[gd_scene load_steps=1 format=3 uid='uid://dummy']"
        writer.add_bytes("res://main.tscn", sample_data)
        writer.build()
        pck_bytes = standalone_pck.read_bytes()
        pck_size = len(pck_bytes)

        # 2. Create simulated EXE: [Dummy PE machine code 1024 bytes] + [PCK bytes] + [Trailer 12 bytes]
        dummy_exe_path = self.base_path / "Oyun.exe"
        dummy_pe_header = b"\x4D\x5A" + b"\x90" * 1022  # 'MZ' header followed by NOPs
        trailer_bytes = struct.pack("<Q", pck_size) + PCK_MAGIC

        with open(dummy_exe_path, "wb") as f:
            f.write(dummy_pe_header)
            f.write(pck_bytes)
            f.write(trailer_bytes)

        # 3. Read simulated EXE with PCKReader
        reader = PCKReader(dummy_exe_path)
        reader.open()

        self.assertTrue(reader.is_embedded)
        self.assertEqual(reader.pck_offset, len(dummy_pe_header))
        self.assertEqual(reader.engine_version, (4, 1, 0))
        self.assertIn("res://main.tscn", reader.entries)
        self.assertEqual(reader.read_file_bytes("res://main.tscn"), sample_data)

    def test_invalid_magic_rejection(self) -> None:
        """Ensures an invalid file throws a clear ValueError rather than crashing."""
        corrupt_file = self.base_path / "invalid.pck"
        corrupt_file.write_bytes(b"NOT_A_VALID_PCK_HEADER_DATA_1234567890")

        reader = PCKReader(corrupt_file)
        with self.assertRaises(ValueError):
            reader.open()

    def test_disk_file_streaming_and_md5_tampering(self) -> None:
        """Tests streaming files from physical disk and verifying MD5 corruption detection."""
        # Create a real source file on disk
        real_file = self.base_path / "original_text.txt"
        test_payload = b"A" * 65536  # 64 KB of data
        real_file.write_bytes(test_payload)

        pck_file = self.base_path / "disk_stream.pck"
        writer = PCKWriter(
            output_path=pck_file,
            format_version=PCK_FORMAT_VERSION_2,
            engine_version=(4, 2, 0),
        )
        writer.add_file("res://texts/readme.txt", real_file)
        writer.build()

        reader = PCKReader(pck_file)
        reader.open()
        self.assertEqual(len(reader.entries), 1)

        # Successful extraction
        dest_clean = self.base_path / "clean_extracted.txt"
        success = reader.extract_file("res://texts/readme.txt", dest_clean, verify_md5=True)
        self.assertTrue(success)
        self.assertEqual(dest_clean.read_bytes(), test_payload)

        # Corrupt the entry MD5 in memory to simulate bit rot / tampering
        entry = reader.entries["res://texts/readme.txt"]
        corrupted_entry = reader.entries["res://texts/readme.txt"].__class__(
            path=entry.path,
            offset=entry.offset,
            size=entry.size,
            md5=b"\x00" * 16,  # bogus hash
            flags=entry.flags,
        )
        reader.entries["res://texts/readme.txt"] = corrupted_entry

        dest_tampered = self.base_path / "tampered_extracted.txt"
        tamper_success = reader.extract_file("res://texts/readme.txt", dest_tampered, verify_md5=True)
        self.assertFalse(tamper_success)


if __name__ == "__main__":
    unittest.main()
