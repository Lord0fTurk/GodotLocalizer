"""Native .gdc constant-table reader, built from the tokenizer buffer layout (Godot 4.3+)."""

import struct
import tempfile
import unittest
from pathlib import Path

from src.core.binary.gdc import GdcError, extract_string_constants, is_compiled_script
from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter
from src.core.pipeline.harvest import harvest_compiled_scripts_native
from src.harvesters.script_harvester import ScriptHarvester


def ident(name: str) -> bytes:
    """Identifiers are UTF-32 code points with every byte XORed with 0xB6."""
    raw = b"".join(struct.pack("<I", ord(c)) for c in name)
    return struct.pack("<I", len(name)) + bytes(b ^ 0xB6 for b in raw)


def v_str(text: str, kind: int = 4) -> bytes:
    raw = text.encode()
    return struct.pack("<II", kind, len(raw)) + raw + b"\x00" * (-len(raw) % 4)


def v_int(value: int) -> bytes:
    return struct.pack("<Ii", 2, value)


def v_int64(value: int) -> bytes:
    return struct.pack("<Iq", 2 | (1 << 16), value)


def v_float(value: float) -> bytes:
    return struct.pack("<If", 3, value)


def v_vec2(x: float, y: float) -> bytes:
    return struct.pack("<Iff", 5, x, y)


def v_node_path(*names: str) -> bytes:
    body = struct.pack("<III", len(names) | 0x80000000, 0, 0)
    for name in names:
        raw = name.encode()
        body += struct.pack("<I", len(raw)) + raw + b"\x00" * (-len(raw) % 4)
    return struct.pack("<I", 22) + body


def gdsc(constants: list[bytes], identifiers: tuple[str, ...] = ("print", "text"), version: int = 101,
         compress: bool = False) -> bytes:
    payload = struct.pack("<IIII", len(identifiers), len(constants), 0, 0)
    payload += b"".join(ident(i) for i in identifiers) + b"".join(constants)
    if compress:
        from compression import zstd

        return b"GDSC" + struct.pack("<II", version, len(payload)) + zstd.compress(payload)
    return b"GDSC" + struct.pack("<II", version, 0) + payload


CONSTANTS = [
    v_str("Game Over"), v_int(3), v_str("Press Start"), v_float(1.5), v_vec2(1, 2), v_int64(2**40),
    v_node_path("Player", "Sprite"), v_str("unicode: çğış", 21), v_str("Game Over"),
]


class TestNativeGdc(unittest.TestCase):
    def test_reads_strings_skipping_other_constants(self) -> None:
        self.assertEqual(
            extract_string_constants(gdsc(CONSTANTS)), ["Game Over", "Press Start", "unicode: çğış"]
        )

    def test_zstd_compressed_payload(self) -> None:
        try:
            data = gdsc(CONSTANTS, compress=True)
        except ImportError:
            self.skipTest("zstd is not available")
        self.assertEqual(extract_string_constants(data)[0], "Game Over")

    def test_rejects_other_formats(self) -> None:
        self.assertFalse(is_compiled_script(b"GDSC" + struct.pack("<II", 13, 0)))  # Godot 4.0-4.2 layout
        self.assertFalse(is_compiled_script(b"RSRC" + b"\x00" * 20))
        for bad in (b"GDSC" + struct.pack("<II", 101, 0) + b"\x00" * 4, gdsc([struct.pack("<I", 99)])):
            with self.assertRaises(GdcError):
                extract_string_constants(bad)

    def test_fragments_and_log_lines_are_not_player_text(self) -> None:
        for text in (' (seen=', "Build Base Directory: ", " ; response code: ", "INFO: loaded", "*.png"):
            self.assertFalse(ScriptHarvester.is_player_text(text), text)
        for text in ("Game Over", "Press any key to continue", "Score: %d"):
            self.assertTrue(ScriptHarvester.is_player_text(text), text)

    def test_pack_level_harvest_skips_addons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = PCKWriter(Path(tmp) / "g.pck", engine_version=(4, 6, 0))
            writer.add_bytes("res://ui/menu.gdc", gdsc([v_str("Start Adventure")]))
            writer.add_bytes("res://addons/tool/panel.gdc", gdsc([v_str("Editor Panel Title")]))
            writer.add_bytes("res://old.gdc", b"GDSC" + struct.pack("<II", 13, 0))
            writer.build()
            reader = PCKReader(Path(tmp) / "g.pck")
            reader.open()
            entries = harvest_compiled_scripts_native(reader)
        self.assertEqual([(e.source_text, e.source_file) for e in entries], [("Start Adventure", "res://ui/menu.gd")])


if __name__ == "__main__":
    unittest.main()
