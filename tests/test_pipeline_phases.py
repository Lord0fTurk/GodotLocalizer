"""Pipeline phase tests that run offline: harvest -> (fake) translation -> deploy, plus font handling."""

import json
import tempfile
import unittest
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from src.core.font_engine.glyph_coverage import GlyphCoverageChecker
from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter
from src.core.pipeline.orchestrator import GodotTranslationPipeline
from src.patcher.bootstrap_script import TRANSLATIONS_RES_PATH, BootstrapScriptGenerator
from src.patcher.overlay_patcher import OverlayPatcher
from tests.rsrc_builder import menu_scene


def make_font(path: Path, chars: str) -> None:
    """Writes a minimal TrueType font whose cmap contains exactly ``chars``."""
    order = [".notdef", *[f"g{ord(c):04x}" for c in chars]]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord(c): f"g{ord(c):04x}" for c in chars})
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((0, 500))
    pen.lineTo((500, 500))
    pen.closePath()
    glyph = pen.glyph()
    fb.setupGlyf(dict.fromkeys(order, glyph))
    fb.setupHorizontalMetrics({name: (600, 0) for name in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "T", "styleName": "R"})
    fb.setupOS2()
    fb.setupPost()
    fb.save(str(path))


class TestPipelinePhases(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        writer = PCKWriter(self.dir / "Game.pck", engine_version=(4, 2, 0))
        writer.add_bytes("res://menu.scn", menu_scene())
        writer.add_bytes("res://tex.ctex", b"\x00" * 64)
        writer.build()
        self.pipeline = GodotTranslationPipeline.__new__(GodotTranslationPipeline)
        self.pipeline.pck_key = ""
        self.pipeline.font_path = ""
        self.pipeline.rtl_shaping = True
        self.pipeline.last_harvest_count = 0

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_phases(self, target: str = "tr") -> tuple[PCKReader, list]:
        profile = self.pipeline.profile_game(str(self.dir))  # folder in, game package picked
        reader = PCKReader(profile.game_path)
        reader.open()
        by_key, unique = self.pipeline._harvest(reader, "auto", None)
        self.pipeline._apply_translations(
            by_key, unique, {"Hello World": "Merhaba Dünya", "Start Game": "Oyunu Başlat"}, target, True
        )
        entries = list(by_key.values())
        self.pipeline._deploy(reader, profile, entries, target, "overlay")
        return reader, entries

    def test_binary_scene_text_is_shipped_through_the_runtime_table(self) -> None:
        _reader, entries = self._run_phases()
        self.assertEqual(self.pipeline.last_harvest_count, 3)
        self.assertEqual(sum(1 for e in entries if e.translation), 2)

        patch = PCKReader(self.dir / OverlayPatcher.DEFAULT_PATCH_PCK)
        patch.open()
        table = json.loads(patch.read_file_bytes(TRANSLATIONS_RES_PATH))
        self.assertEqual(table["messages"], {"Hello World": "Merhaba Dünya", "Start Game": "Oyunu Başlat"})
        self.assertTrue((self.dir / "override.cfg").exists())

    def test_custom_font_is_injected_and_checked(self) -> None:
        font = self.dir / "f.ttf"
        make_font(font, "abc")
        self.pipeline.font_path = str(font)
        with self.assertLogs("src.core.pipeline.orchestrator", level="WARNING") as logs:
            self._run_phases()
        self.assertTrue(any("missing glyphs" in line for line in logs.output))

        patch = PCKReader(self.dir / OverlayPatcher.DEFAULT_PATCH_PCK)
        patch.open()
        self.assertEqual(patch.read_file_bytes("res://patch_assets/fonts/LocalizedFont.ttf"), font.read_bytes())

    def test_glyph_checker_languages(self) -> None:
        font = self.dir / "g.ttf"
        make_font(font, "abcdefghijklmnopqrstuvwxyz")
        for code in ("tr", "ar", "ja", "zh-CN", "pt_BR"):
            self.assertFalse(GlyphCoverageChecker.check_font_file(font, code).is_fully_covered, code)
        with self.assertRaises(ValueError):
            GlyphCoverageChecker.check_font_file(font, "th")

    def test_bootstrap_font_code_uses_runtime_font_data(self) -> None:
        g4 = BootstrapScriptGenerator.generate_godot4_bootstrap(font_res_path="res://f.ttf")
        self.assertIn("FontFile.new()", g4)
        self.assertIn("FileAccess.get_file_as_bytes", g4)
        self.assertNotIn("ThemeDB.fallback_font = ", g4)  # never replace the engine font
        self.assertNotIn("load(font_path)", g4)  # raw fonts in a pack have no .import metadata
        g3 = BootstrapScriptGenerator.generate_godot3_bootstrap(font_res_path="res://f.ttf")
        self.assertIn("DynamicFontData.new()", g3)
        self.assertIn("add_fallback", g3)
        self.assertNotIn("DynamicFontData", BootstrapScriptGenerator.generate_godot3_bootstrap(font_res_path=None))


if __name__ == "__main__":
    unittest.main()
