"""Unit tests for narrative parsers and string harvesters."""

import unittest

from src.harvesters.scene_harvester import SceneHarvester
from src.harvesters.script_harvester import ScriptHarvester
from src.harvesters.translation_harvester import TranslationHarvester
from src.parsers.dialogic import DialogicParser
from src.parsers.dialogue_manager import DialogueManagerParser


class TestHarvestersAndParsers(unittest.TestCase):
    """Verifies extraction and reconstruction across dialogue and scene formats."""

    def test_dialogue_manager_harvest_and_replace(self) -> None:
        """Tests parsing Nathan Hoad .dialogue files and applying translations."""
        sample_dialogue = (
            "~ start\n"
            "Nathan: Welcome traveler! What brings you here?\n"
            "- Looking for adventures => quest_start\n"
            "- Just passing through => END\n"
        )
        entries = DialogueManagerParser.parse_content(sample_dialogue, "intro.dialogue")
        self.assertEqual(len(entries), 3)

        self.assertEqual(entries[0].context, "Nathan")
        self.assertEqual(entries[0].source_text, "Welcome traveler! What brings you here?")
        self.assertEqual(entries[1].context, "Choice")
        self.assertEqual(entries[1].source_text, "Looking for adventures")

        # Test in-place translation replacement
        translations = {
            entries[0].key: "Hoş geldin gezgin! Seni buraya ne getirdi?",
            entries[1].key: "Macera arıyorum",
        }
        reconstructed = DialogueManagerParser.apply_translations(
            sample_dialogue, translations, "intro.dialogue"
        )
        self.assertIn("Nathan: Hoş geldin gezgin! Seni buraya ne getirdi?", reconstructed)
        self.assertIn("- Macera arıyorum => quest_start", reconstructed)
        self.assertIn("- Just passing through => END", reconstructed)

    def test_dialogic_dtl_harvest(self) -> None:
        """Tests extracting lines from Dialogic 2.x .dtl timelines."""
        sample_dtl = (
            "join Knight 1\n"
            "Knight (happy): The kingdom is at peace.\n"
            "- Thank goodness\n"
            "- Are you certain? [then=\"check_castle\"]\n"
        )
        entries = DialogicParser.parse_dtl(sample_dtl, "timeline.dtl")
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0].source_text, "The kingdom is at peace.")
        self.assertEqual(entries[0].context, "Knight")
        self.assertEqual(entries[1].source_text, "Thank goodness")

    def test_dialogic_d1_json_harvest(self) -> None:
        """Tests parsing Dialogic 1.x timeline JSON events."""
        sample_json = """
        {
            "events": [
                {"event_id": "dialogic_001", "character": "Wizard", "text": "Take this spell!"},
                {"event_id": "dialogic_010", "choice": "I accept"}
            ]
        }
        """
        entries = DialogicParser.parse_d1_json(sample_json, "wizard.json")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].source_text, "Take this spell!")
        self.assertEqual(entries[0].context, "Wizard")
        self.assertEqual(entries[1].source_text, "I accept")

    def test_scene_harvester_tscn(self) -> None:
        """Tests extracting UI text from Godot .tscn scene tree."""
        sample_tscn = (
            '[gd_scene format=3 uid="uid://test"]\n'
            '[node name="MainMenu" type="Control"]\n'
            '[node name="TitleLabel" type="Label" parent="."]\n'
            'text = "Godot Adventure RPG"\n'
            '[node name="StartBtn" type="Button" parent="."]\n'
            'text = "Start New Game"\n'
            'tooltip_text = "Click to enter the dungeon"\n'
        )
        entries = SceneHarvester.harvest_tscn(sample_tscn, "res://scenes/main.tscn")
        self.assertEqual(len(entries), 3)

        texts = [e.source_text for e in entries]
        self.assertIn("Godot Adventure RPG", texts)
        self.assertIn("Start New Game", texts)
        self.assertIn("Click to enter the dungeon", texts)

    def test_translation_csv_and_po(self) -> None:
        """Tests official Godot CSV and PO catalog parsing."""
        csv_data = "id,en,tr\nKEY_START,Start Game,Oyuna Basla\nKEY_QUIT,Quit,Cikis"
        csv_entries = TranslationHarvester.harvest_csv(csv_data, "res://loc.csv", "en")
        self.assertEqual(len(csv_entries), 2)
        self.assertEqual(csv_entries[0].source_text, "Start Game")

        po_data = (
            'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
            'msgid "Settings"\nmsgstr "Ayarlar"\n\n'
            'msgid "Audio Volume"\nmsgstr "Ses Duzeyi"\n'
        )
        po_entries = TranslationHarvester.harvest_po(po_data, "res://game.po")
        self.assertEqual(len(po_entries), 2)
        self.assertEqual(po_entries[0].source_text, "Settings")
        self.assertEqual(po_entries[0].translation, "Ayarlar")

    def test_script_harvester_gd(self) -> None:
        """Tests harvesting string literals from GDScript source code."""
        gd_code = (
            'extends Node\n'
            'func _ready():\n'
            '    print("res://ignore_me.tscn")\n'
            '    var dialogue = "You have defeated the goblin!"\n'
            '    show_message("Victory Achieved!")\n'
        )
        entries = ScriptHarvester.harvest_gd(gd_code, "res://scripts/combat.gd")
        texts = [e.source_text for e in entries]
        self.assertIn("You have defeated the goblin!", texts)
        self.assertIn("Victory Achieved!", texts)
        self.assertNotIn("res://ignore_me.tscn", texts)


if __name__ == "__main__":
    unittest.main()
