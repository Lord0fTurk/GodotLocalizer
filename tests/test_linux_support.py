"""Linux/BSD exports: embedded PCK in an ELF section, binary discovery and launcher lookup."""

import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.backend.game_files import find_game_executable, has_game_executable
from src.backend.helpers import local_path
from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter
from src.core.pipeline.orchestrator import GodotTranslationPipeline


def build_pck(path: Path) -> bytes:
    writer = PCKWriter(path, engine_version=(4, 3, 0))
    writer.add_bytes("res://a.txt", b"data")
    writer.build()
    return path.read_bytes()


def build_elf(pck: bytes, bits: int = 64, order: str = "<") -> bytes:
    """A minimal ELF with a 'pck' section holding the pack, as Godot 4.3+ writes for Linux exports."""
    ehsize = 64 if bits == 64 else 52
    names = b"\x00pck\x00.shstrtab\x00"
    names_at = ehsize
    pck_at = (names_at + len(names) + 15) // 16 * 16
    shoff = (pck_at + len(pck) + 15) // 16 * 16
    word = "Q" if bits == 64 else "I"
    shentsize = 64 if bits == 64 else 40

    def header(name: int, offset: int, size: int) -> bytes:
        raw = bytearray(shentsize)
        struct.pack_into(order + "I", raw, 0, name)
        at = 24 if bits == 64 else 16
        struct.pack_into(order + word + word, raw, at, offset, size)
        return bytes(raw)

    ident = b"\x7fELF" + bytes([2 if bits == 64 else 1, 1 if order == "<" else 2, 1]) + b"\x00" * 9
    if bits == 64:
        rest = bytearray(48)
        struct.pack_into(order + "Q", rest, 24, shoff)
        struct.pack_into(order + "HHH", rest, 42, shentsize, 3, 2)
    else:
        rest = bytearray(36)
        struct.pack_into(order + "I", rest, 16, shoff)
        struct.pack_into(order + "HHH", rest, 30, shentsize, 3, 2)
    blob = bytearray(ident + bytes(rest))
    blob.extend(names)
    blob.extend(b"\x00" * (pck_at - len(blob)))
    blob.extend(pck)
    blob.extend(b"\x00" * (shoff - len(blob)))
    blob.extend(header(0, 0, 0) + header(1, pck_at, len(pck)) + header(5, names_at, len(names)))
    return bytes(blob)


class TestElfEmbeddedPck(unittest.TestCase):
    def test_pck_section_is_found_for_every_elf_flavour(self) -> None:
        for bits, order in ((64, "<"), (32, "<"), (64, ">")):
            with tempfile.TemporaryDirectory() as tmp:
                pck = build_pck(Path(tmp) / "inner.pck")
                binary = Path(tmp) / "Game.x86_64"
                binary.write_bytes(build_elf(pck, bits, order))
                reader = PCKReader(binary)
                reader.open()
                self.assertTrue(reader.is_embedded, (bits, order))
                self.assertEqual(reader.read_file_bytes("res://a.txt", verify_md5=True), b"data", (bits, order))

    def test_plain_elf_without_pack_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "tool"
            binary.write_bytes(b"\x7fELF" + b"\x02\x01\x01" + b"\x00" * 200)
            with self.assertRaises(ValueError):
                PCKReader(binary).open()


class TestLinuxDiscovery(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_pipeline_picks_the_elf_binary_that_carries_a_pack(self) -> None:
        pck = build_pck(self.dir / "inner.pck")
        (self.dir / "inner.pck").unlink()
        (self.dir / "Game.x86_64").write_bytes(build_elf(pck))
        (self.dir / "helper").write_bytes(b"\x7fELF" + b"\x00" * 64)
        (self.dir / "readme.txt").write_text("hi")
        pipeline = GodotTranslationPipeline.__new__(GodotTranslationPipeline)
        pipeline.pck_key = ""
        self.assertEqual(pipeline._pick_game_file(self.dir).name, "Game.x86_64")

    def test_launcher_is_found_next_to_the_pck(self) -> None:
        (self.dir / "Game.pck").write_bytes(b"GDPC")
        (self.dir / "Other.x86_64").write_bytes(b"\x7fELF" + b"\x00" * 16)
        (self.dir / "Game.x86_64").write_bytes(b"\x7fELF" + b"\x00" * 16)
        (self.dir / "notes.txt").write_text("x")
        self.assertEqual(find_game_executable(str(self.dir / "Game.pck")).name, "Game.x86_64")
        self.assertTrue(has_game_executable(str(self.dir / "Game.pck")))

    def test_shell_launcher_and_missing_launcher(self) -> None:
        (self.dir / "Game.pck").write_bytes(b"GDPC")
        self.assertIsNone(find_game_executable(str(self.dir / "Game.pck")))
        (self.dir / "Game.sh").write_text("#!/bin/sh\n")
        self.assertEqual(find_game_executable(str(self.dir / "Game.pck")).name, "Game.sh")


class TestFileUrlOnPosix(unittest.TestCase):
    def test_fallback_keeps_the_leading_slash(self) -> None:
        with mock.patch("src.backend.helpers.QUrl") as qurl, mock.patch("src.backend.helpers.os.name", "posix"):
            qurl.return_value.toLocalFile.return_value = ""
            self.assertEqual(local_path("file:///home/me/My%20Game/game.pck"), "/home/me/My Game/game.pck")
        self.assertTrue(os.name in ("nt", "posix"))


if __name__ == "__main__":
    unittest.main()
