"""Games exported with a custom Godot build use another 4-byte signature; the error must say so."""

import struct
import tempfile
import unittest
from pathlib import Path

from src.core.pck.constants import PCK_MAGIC
from src.core.pck.reader import CustomPckSignatureError, PCKReader
from src.core.pipeline.orchestrator import GodotTranslationPipeline
from tests.test_linux_support import build_elf, build_pck


class TestCustomSignature(unittest.TestCase):
    def test_elf_section_with_another_signature_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pck = build_pck(Path(tmp) / "inner.pck").replace(PCK_MAGIC, b"KCVK", 1)
            binary = Path(tmp) / "Game.x86_64"
            binary.write_bytes(build_elf(pck))
            with self.assertRaises(CustomPckSignatureError) as caught:
                PCKReader(binary).open()
            self.assertEqual(caught.exception.signature, b"KCVK")
            self.assertIn("custom Godot build", str(caught.exception))

    def test_trailer_with_another_signature_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pck = build_pck(Path(tmp) / "inner.pck").replace(PCK_MAGIC, b"KCVK", 1)
            binary = Path(tmp) / "Game.exe"
            binary.write_bytes(b"MZ" + b"\x00" * 600 + pck + struct.pack("<Q", len(pck)) + b"KCVK")
            with self.assertRaises(CustomPckSignatureError):
                PCKReader(binary).open()

    def test_folder_scan_surfaces_the_reason_instead_of_a_generic_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pck = build_pck(Path(tmp) / "inner.pck").replace(PCK_MAGIC, b"KCVK", 1)
            (Path(tmp) / "inner.pck").unlink()
            (Path(tmp) / "Game.x86_64").write_bytes(build_elf(pck))
            pipeline = GodotTranslationPipeline.__new__(GodotTranslationPipeline)
            pipeline.pck_key = ""
            with self.assertRaises(CustomPckSignatureError):
                pipeline._pick_game_file(Path(tmp))

    def test_ordinary_non_godot_binary_keeps_the_generic_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "tool.exe"
            binary.write_bytes(b"MZ" + b"\x00" * 2000)
            with self.assertRaises(ValueError) as caught:
                PCKReader(binary).open()
            self.assertNotIsInstance(caught.exception, CustomPckSignatureError)


if __name__ == "__main__":
    unittest.main()
