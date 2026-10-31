"""Unit tests for patch engines (Overlay, Repack, and Autoload Bootstrap)."""

import tempfile
import unittest
from pathlib import Path

from src.core.pck.constants import PCK_FORMAT_VERSION_2
from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter
from src.patcher.bootstrap_script import BootstrapScriptGenerator
from src.patcher.overlay_patcher import OverlayPatcher
from src.patcher.repack_patcher import RepackPatcher


class TestPatcherEngine(unittest.TestCase):
    """Verifies non-destructive overlay patching and complete PCK repacking."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.game_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_bootstrap_script_generator(self) -> None:
        """Verifies Godot 3 and 4 autoload script syntax."""
        g4_script = BootstrapScriptGenerator.generate_godot4_bootstrap(
            pck_file_name="godotlocalizer_patch.pck",
            target_locale="tr",
            font_res_path="res://font.ttf",
        )
        self.assertIn("extends Node", g4_script)
        self.assertIn('ProjectSettings.load_resource_pack(patch_path, true)', g4_script)
        self.assertIn('TranslationServer.set_locale("tr")', g4_script)
        self.assertIn("ThemeDB.get_project_theme()", g4_script)

        g3_script = BootstrapScriptGenerator.generate_godot3_bootstrap(
            pck_file_name="godotlocalizer_patch.pck", target_locale="tr"
        )
        self.assertIn("File.new()", g3_script)
        self.assertIn('TranslationServer.set_locale("tr")', g3_script)

    def test_overlay_patcher_deploy_and_remove(self) -> None:
        """Tests Method A: creating patch.pck, override.cfg, and clean removal."""
        patcher = OverlayPatcher(game_dir=self.game_dir, is_godot4=True)

        localized_data = {
            "res://dialogue/intro.dialogue": b"Nathan: Merhaba Dunya!\n",
        }
        dummy_font = b"DUMMY_TTF_FONT_BYTES_12345"

        success = patcher.build_overlay_patch(
            localized_files=localized_data,
            target_locale="tr",
            font_bytes=dummy_font,
        )
        self.assertTrue(success)

        # Check deployed files
        patch_pck = self.game_dir / "godotlocalizer_patch.pck"
        override_cfg = self.game_dir / "override.cfg"
        bootstrap_gd = self.game_dir / "godotlocalizer_bootstrap.gd"

        self.assertTrue(patch_pck.exists())
        self.assertTrue(override_cfg.exists())
        self.assertTrue(bootstrap_gd.exists())

        # Inspect override.cfg contents
        cfg_content = override_cfg.read_text(encoding="utf-8")
        self.assertIn("[autoload]", cfg_content)
        self.assertIn('GodotLocalizerBootstrap="*res://godotlocalizer_bootstrap.gd"', cfg_content)

        # Read patch.pck and verify payload
        reader = PCKReader(patch_pck)
        reader.open()
        self.assertIn("res://dialogue/intro.dialogue", reader.entries)
        self.assertIn("res://patch_assets/fonts/LocalizedFont.ttf", reader.entries)
        self.assertEqual(
            reader.read_file_bytes("res://dialogue/intro.dialogue"),
            b"Nathan: Merhaba Dunya!\n",
        )

        # Test clean uninstall
        patcher.remove_patch()
        self.assertFalse(patch_pck.exists())
        self.assertFalse(override_cfg.exists())
        self.assertFalse(bootstrap_gd.exists())

    def test_repack_patcher(self) -> None:
        """Tests Method B: rebuilding an entire PCK with updated files."""
        # 1. Create a base vanilla PCK
        base_pck = self.game_dir / "base.pck"
        writer = PCKWriter(
            output_path=base_pck,
            format_version=PCK_FORMAT_VERSION_2,
            engine_version=(4, 2, 0),
        )
        writer.add_bytes("res://untouched.tscn", b"ORIGINAL_SCENE_DATA")
        writer.add_bytes("res://dialogue.csv", b"id,en\nhello,Hello")
        writer.build()

        # 2. Repack with localized dialogue
        repacked_pck = self.game_dir / "repacked.pck"
        updated = {
            "res://dialogue.csv": b"id,en,tr\nhello,Hello,Merhaba",
        }
        extra = {
            "res://extra_font.ttf": b"FONT_DATA",
        }

        total_files = RepackPatcher.repack(
            source_pck_path=base_pck,
            output_pck_path=repacked_pck,
            updated_files=updated,
            extra_files=extra,
        )
        self.assertEqual(total_files, 3)

        # 3. Verify repacked contents
        reader = PCKReader(repacked_pck)
        reader.open()
        self.assertEqual(
            reader.read_file_bytes("res://untouched.tscn"),
            b"ORIGINAL_SCENE_DATA",
        )
        self.assertEqual(
            reader.read_file_bytes("res://dialogue.csv"),
            b"id,en,tr\nhello,Hello,Merhaba",
        )
        self.assertEqual(
            reader.read_file_bytes("res://extra_font.ttf"),
            b"FONT_DATA",
        )


if __name__ == "__main__":
    unittest.main()
