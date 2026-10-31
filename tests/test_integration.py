"""End-to-End integration tests for GodotLocalizer workflow."""

import struct
import tempfile
import unittest
from pathlib import Path

from src.core.pck.constants import PCK_FORMAT_VERSION_2, PCK_MAGIC
from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter
from src.harvesters.scene_harvester import SceneHarvester
from src.harvesters.translation_harvester import TranslationHarvester
from src.parsers.dialogue_manager import DialogueManagerParser
from src.patcher.overlay_patcher import OverlayPatcher
from src.patcher.repack_patcher import RepackPatcher


class TestEndToEndWorkflow(unittest.TestCase):
    """Simulates a complete game localization workflow from binary analysis to patch delivery."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.game_root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_full_localization_lifecycle(self) -> None:
        """Runs the entire pipeline: Embedded EXE -> Harvest -> Translate -> Overlay Patch -> Verify."""
        # 1. Simulate an exported Godot game: Oyun.exe with embedded PCK
        raw_pck_path = self.game_root / "temp_vanilla.pck"
        writer = PCKWriter(
            output_path=raw_pck_path,
            format_version=PCK_FORMAT_VERSION_2,
            engine_version=(4, 2, 0),
        )

        scene_content = (
            '[gd_scene format=3 uid="uid://main"]\n'
            '[node name="HUD" type="Control"]\n'
            '[node name="ScoreLabel" type="Label" parent="."]\n'
            'text = "High Score: 9999"\n'
        )
        dialogue_content = (
            "~ quest\n"
            "King: Save our kingdom from the dragon!\n"
            "- I will slay the beast! => accept_quest\n"
        )
        csv_content = "id,en\nMENU_OPTIONS,Options\nMENU_EXIT,Exit Game"

        writer.add_bytes("res://scenes/hud.tscn", scene_content.encode("utf-8"))
        writer.add_bytes("res://narrative/king.dialogue", dialogue_content.encode("utf-8"))
        writer.add_bytes("res://translations.csv", csv_content.encode("utf-8"))
        writer.build()

        pck_bytes = raw_pck_path.read_bytes()
        raw_pck_path.unlink()

        # Build simulated PE executable with 12-byte trailer
        game_exe_path = self.game_root / "Oyun.exe"
        dummy_pe_blob = b"MZ" + b"\x90" * 2048  # Simulated PE header
        trailer = struct.pack("<Q", len(pck_bytes)) + PCK_MAGIC

        with open(game_exe_path, "wb") as f:
            f.write(dummy_pe_blob)
            f.write(pck_bytes)
            f.write(trailer)

        # 2. Open game with PCKReader and verify embedded discovery
        reader = PCKReader(game_exe_path)
        reader.open()
        self.assertTrue(reader.is_embedded)
        self.assertEqual(len(reader.entries), 3)

        # 3. Harvest strings from all channels
        hud_text = reader.read_file_bytes("res://scenes/hud.tscn").decode("utf-8")
        dialogue_text = reader.read_file_bytes("res://narrative/king.dialogue").decode("utf-8")
        csv_text = reader.read_file_bytes("res://translations.csv").decode("utf-8")

        scene_entries = SceneHarvester.harvest_tscn(hud_text, "res://scenes/hud.tscn")
        dialogue_entries = DialogueManagerParser.parse_content(dialogue_text, "res://narrative/king.dialogue")
        csv_entries = TranslationHarvester.harvest_csv(csv_text, "res://translations.csv")

        self.assertEqual(len(scene_entries), 1)
        self.assertEqual(scene_entries[0].source_text, "High Score: 9999")

        self.assertEqual(len(dialogue_entries), 2)
        self.assertEqual(dialogue_entries[0].source_text, "Save our kingdom from the dragon!")

        self.assertEqual(len(csv_entries), 2)
        self.assertEqual(csv_entries[0].source_text, "Options")

        # 4. Perform translations
        dialogue_map = {
            dialogue_entries[0].key: "Krallığımızı ejderhadan kurtar!",
            dialogue_entries[1].key: "Canavarı alt edeceğim!",
        }
        translated_dialogue = DialogueManagerParser.apply_translations(
            dialogue_text, dialogue_map, "res://narrative/king.dialogue"
        )
        translated_csv = "id,en,tr\nMENU_OPTIONS,Options,Seçenekler\nMENU_EXIT,Exit Game,Çıkış"

        localized_files = {
            "res://narrative/king.dialogue": translated_dialogue.encode("utf-8"),
            "res://translations.csv": translated_csv.encode("utf-8"),
        }

        # 5. Method A: Deploy Overlay Patch
        patcher = OverlayPatcher(game_dir=self.game_root, is_godot4=True)
        deploy_ok = patcher.build_overlay_patch(
            localized_files=localized_files,
            target_locale="tr",
        )
        self.assertTrue(deploy_ok)

        patch_pck = self.game_root / "godotlocalizer_patch.pck"
        override_cfg = self.game_root / "override.cfg"
        self.assertTrue(patch_pck.exists())
        self.assertTrue(override_cfg.exists())

        # Verify patch contents
        patch_reader = PCKReader(patch_pck)
        patch_reader.open()
        self.assertIn("res://narrative/king.dialogue", patch_reader.entries)
        patched_dialogue = patch_reader.read_file_bytes("res://narrative/king.dialogue").decode("utf-8")
        self.assertIn("Krallığımızı ejderhadan kurtar!", patched_dialogue)

        # 6. Method B: Clean Repack
        repacked_pck = self.game_root / "Oyun_CleanRepack.pck"
        repack_count = RepackPatcher.repack(
            source_pck_path=game_exe_path,
            output_pck_path=repacked_pck,
            updated_files=localized_files,
        )
        self.assertEqual(repack_count, 3)

        repack_reader = PCKReader(repacked_pck)
        repack_reader.open()
        self.assertEqual(len(repack_reader.entries), 3)
        self.assertIn("Krallığımızı ejderhadan kurtar!", repack_reader.read_file_bytes("res://narrative/king.dialogue").decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
