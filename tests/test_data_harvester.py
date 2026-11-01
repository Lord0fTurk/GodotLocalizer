import tempfile
import unittest
from pathlib import Path

from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter
from src.core.pipeline.analysis import find_unscanned_text_files
from src.core.pipeline.harvest import (
    KIND_CFG,
    KIND_TRES,
    KIND_TXT,
    KIND_YAML,
    classify,
)
from src.harvesters.data_harvester import apply_data_translations, harvest_data_file
from src.harvesters.script_harvester import ScriptHarvester
from src.parsers.dialogic import DialogicParser


def _translate(kind: str, content: str, path: str) -> tuple[list[str], str]:
    entries = harvest_data_file(kind, content, path)
    lookup = {e.key: f"T:{e.source_text}" for e in entries}
    return [e.source_text for e in entries], apply_data_translations(kind, content, lookup, path)


class TestClassify(unittest.TestCase):
    def test_data_formats_are_routed(self) -> None:
        self.assertEqual(classify("res://data/events.txt"), KIND_TXT)
        self.assertEqual(classify("res://data/events.yml"), KIND_YAML)
        self.assertEqual(classify("res://data/text.cfg"), KIND_CFG)
        self.assertEqual(classify("res://items/sword.tres"), KIND_TRES)

    def test_licences_plugins_and_configuration_are_skipped(self) -> None:
        for path in (
            "res://LICENSE.txt", "res://README.txt", "res://addons/x/info.txt", "res://export_presets.cfg",
            "res://override.cfg", "res://.godot/uid_cache.cfg",
        ):
            self.assertIsNone(classify(path), path)

    def test_dialogue_manager_tres_keeps_its_own_kind(self) -> None:
        self.assertNotEqual(classify("res://d.dialogue-abc.tres"), KIND_TRES)


class TestTextLines(unittest.TestCase):
    CONTENT = "Names\r\nBay Wilde\r\nThe guard looks at you, unimpressed.\r\n\r\n  You hear a distant scream.\r\n"

    def test_only_sentences_are_taken_and_layout_is_kept(self) -> None:
        texts, out = _translate(KIND_TXT, self.CONTENT, "res://flavor.txt")
        self.assertEqual(texts, ["The guard looks at you, unimpressed.", "You hear a distant scream."])
        self.assertEqual(
            out,
            "Names\r\nBay Wilde\r\nT:The guard looks at you, unimpressed.\r\n\r\n  T:You hear a distant scream.\r\n",
        )


class TestYaml(unittest.TestCase):
    CONTENT = (
        "events:\n"
        "  - id: heist_01\n"
        "    text: \"You slip past the guards, heart pounding.\"  # intro\n"
        "    result: He never saw you coming, did he\n"
        "    note: 'It''s a trap, and you know it.'\n"
        "    body: |\n"
        "      The door creaks open slowly.\n"
        "      Dust fills the air.\n"
        "    weight: 3\n"
        "  - Take the gold and run for the exit\n"
    )

    def test_scalars_block_text_and_list_items_are_found(self) -> None:
        texts, _out = _translate(KIND_YAML, self.CONTENT, "res://e.yaml")
        self.assertEqual(texts, [
            "You slip past the guards, heart pounding.",
            "He never saw you coming, did he",
            "It's a trap, and you know it.",
            "The door creaks open slowly.",
            "Dust fills the air.",
            "Take the gold and run for the exit",
        ])

    def test_rewrite_keeps_keys_comments_and_quoting(self) -> None:
        _texts, out = _translate(KIND_YAML, self.CONTENT, "res://e.yaml")
        self.assertIn('    text: "T:You slip past the guards, heart pounding."  # intro\n', out)
        self.assertIn("    result: T:He never saw you coming, did he\n", out)
        self.assertIn("    note: 'T:It''s a trap, and you know it.'\n", out)
        self.assertIn("      T:The door creaks open slowly.\n", out)
        self.assertIn("    weight: 3\n  - T:Take the gold", out)
        self.assertIn("  - id: heist_01\n", out)


class TestGodotTextResources(unittest.TestCase):
    TRES = (
        '[gd_resource type="Resource" script_class="Event" load_steps=2 format=3]\n\n'
        '[ext_resource type="Script" path="res://event.gd" id="1_abc"]\n\n'
        '[resource]\nscript = ExtResource("1_abc")\n'
        'title = "Ambush"\n'
        'description = "Thieves jump out of the shadows, blades drawn."\n'
        'lines = Array[String](["You freeze for a moment.", "Then you reach for your knife.", "res://x.png"])\n'
        'damage = 12\n'
    )

    def test_known_properties_sentences_and_array_items(self) -> None:
        texts, _out = _translate(KIND_TRES, self.TRES, "res://ambush.tres")
        self.assertEqual(texts, [
            "Ambush",
            "Thieves jump out of the shadows, blades drawn.",
            "You freeze for a moment.",
            "Then you reach for your knife.",
        ])

    def test_rewrite_touches_only_translated_literals(self) -> None:
        _texts, out = _translate(KIND_TRES, self.TRES, "res://ambush.tres")
        self.assertIn('title = "T:Ambush"\n', out)
        self.assertIn('["T:You freeze for a moment.", "T:Then you reach for your knife.", "res://x.png"])', out)
        self.assertIn('script = ExtResource("1_abc")\n', out)
        self.assertTrue(out.endswith("damage = 12\n"))

    def test_multiline_strings_and_escapes_round_trip(self) -> None:
        content = '[resource]\ndescription = "First line, quite long.\nSecond \\"line\\" follows here."\n'
        texts, out = _translate(KIND_TRES, content, "res://m.tres")
        self.assertEqual(texts, ['First line, quite long.\nSecond "line" follows here.'])
        self.assertEqual(out, '[resource]\ndescription = "T:First line, quite long.\\nSecond \\"line\\" follows here."\n')

    def test_cfg_values_quoted_and_bare(self) -> None:
        content = '[intro]\ngreeting="Welcome back, traveller."\nhint=Press any key to continue\nversion=3\n'
        texts, out = _translate(KIND_CFG, content, "res://t.cfg")
        self.assertEqual(texts, ["Welcome back, traveller.", "Press any key to continue"])
        self.assertIn('greeting="T:Welcome back, traveller."', out)
        self.assertIn("hint=T:Press any key to continue\n", out)


class TestJsonStringLists(unittest.TestCase):
    DATA = '{"lines": ["You wake up in a cold cell.", "Someone is watching."], "id": ["not text here"], "text": "Hi"}'

    def test_items_of_a_text_field_list_are_harvested(self) -> None:
        entries = DialogicParser.parse_generic_json(self.DATA, "res://c.json", ("lines",))
        self.assertEqual(
            [e.source_text for e in entries], ["You wake up in a cold cell.", "Someone is watching.", "Hi"]
        )
        self.assertEqual(entries[0].key, "res://c.json::lines[0]")

    def test_list_items_are_written_back(self) -> None:
        entries = DialogicParser.parse_generic_json(self.DATA, "res://c.json", ("lines",))
        lookup = {e.key: f"T:{e.source_text}" for e in entries}
        out = DialogicParser.apply_generic_json_translations(self.DATA, lookup, "res://c.json", ("lines",))
        self.assertIn("T:You wake up in a cold cell.", out)
        self.assertIn("T:Someone is watching.", out)
        self.assertIn("not text here", out)


class TestGdScriptText(unittest.TestCase):
    @staticmethod
    def texts(code: str) -> list[str]:
        return [e.source_text for e in ScriptHarvester.harvest_gd(code, "res://a.gd")]

    def test_tr_calls_are_always_player_text(self) -> None:
        code = (
            'label.text = tr("quit")\n'
            "print(tr('you feel a chill'))\n"
            'var s = atr("go back")\n'
            'var n = tr_n("one coin", "many coins", count)\n'
            'var path = str("quit")\n'
        )
        self.assertEqual(self.texts(code), ["quit", "you feel a chill", "go back", "one coin"])

    def test_lowercase_sentences_with_closing_punctuation_count_as_text(self) -> None:
        code = 'var a = "you feel a chill run down your spine."\nvar b = "loaded save slot"\n'
        self.assertEqual(self.texts(code), ["you feel a chill run down your spine."])


class TestUnscannedFiles(unittest.TestCase):
    def test_unknown_text_formats_are_reported_and_known_ones_are_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = PCKWriter(Path(tmp) / "g.pck", engine_version=(4, 2, 0))
            writer.add_bytes("res://text/events.dat", b"The caravan stops at dusk, tired and hungry.\n")
            writer.add_bytes("res://text/more.dat", b"\x00\x01\x02binary")
            writer.add_bytes("res://text/lines.txt", b"The caravan stops at dusk, tired and hungry.\n")
            writer.add_bytes("res://icon.png", b"\x89PNG not text")
            writer.build()
            reader = PCKReader(Path(tmp) / "g.pck")
            reader.open()
            groups = find_unscanned_text_files(reader)
        self.assertEqual([(g.extension, g.files) for g in groups], [(".dat", 2)])
        self.assertEqual(groups[0].sample_path, "res://text/events.dat")


if __name__ == "__main__":
    unittest.main()
