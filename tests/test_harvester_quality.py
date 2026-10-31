"""Edge cases for the text harvesters: strings with '#', multi-line scene values, PO contexts, DM logic."""

import unittest

from src.core.pipeline.harvest import KIND_GD, classify, parse_json_keys
from src.core.syntax_guard import UnitySyntaxGuard
from src.harvesters.scene_harvester import SceneHarvester
from src.harvesters.script_harvester import ScriptHarvester
from src.harvesters.translation_harvester import TranslationHarvester
from src.parsers.dialogic import DialogicParser
from src.parsers.dialogue_manager import DialogueManagerParser


class TestScriptHarvester(unittest.TestCase):
    def texts(self, code: str) -> list[str]:
        return [e.source_text for e in ScriptHarvester.harvest_gd(code, "res://a.gd")]

    def test_hash_inside_strings_is_not_a_comment(self) -> None:
        found = self.texts('label.text = "Item #1 acquired!"  # shown on pickup\n')
        self.assertEqual(found, ["Item #1 acquired!"])

    def test_single_quotes_and_comment_only_strings(self) -> None:
        found = self.texts("msg = 'Press any key to continue'\n# \"Not a real string here\"\n")
        self.assertEqual(found, ["Press any key to continue"])

    def test_identifiers_and_engine_calls_are_ignored(self) -> None:
        code = (
            'var n = get_node("Player Sprite")\n'
            'button.connect("pressed", self, "_on_pressed")\n'
            'add_to_group("enemies")\n'
            'const Scene = preload("res://scene.tscn")\n'
            'if Input.is_action_pressed("move_left"): pass\n'
            'var title = "Game Over"\n'
            'var tag = "Continue"\n'
            'var key = "player_hp"\n'
        )
        self.assertEqual(self.texts(code), ["Game Over", "Continue"])

    def test_gd_files_are_classified(self) -> None:
        self.assertEqual(classify("res://scripts/ui.gd"), KIND_GD)


class TestSceneHarvesterEdgeCases(unittest.TestCase):
    SCENE = (
        '[node name="Root" type="Control"]\r\n'
        '[node name="Opt" type="OptionButton" parent="."]\r\n'
        'popup/item_0/text = "Easy"\r\n'
        'popup/item_1/text = "Hard"\r\n'
        '[node name="Intro" type="Label" parent="."]\r\n'
        'text = "Line one\r\nLine two"\r\n'
        'visible = true\r\n'
    )

    def test_multiline_values_and_indexed_items(self) -> None:
        entries = SceneHarvester.harvest_tscn(self.SCENE, "res://s.tscn")
        found = {e.metadata["property"]: e.source_text for e in entries}
        self.assertEqual(found["popup/item_0/text"], "Easy")
        self.assertEqual(found["popup/item_1/text"], "Hard")
        self.assertEqual(found["text"].replace("\r\n", "\n"), "Line one\nLine two")

    def test_rewrite_keeps_everything_else(self) -> None:
        entries = SceneHarvester.harvest_tscn(self.SCENE, "res://s.tscn")
        lookup = {e.key: f"T:{e.source_text}".replace("\r\n", "\n") for e in entries}
        out = SceneHarvester.apply_tscn_translations(self.SCENE, lookup, "res://s.tscn")
        self.assertIn('popup/item_0/text = "T:Easy"\r\n', out)
        self.assertIn('text = "T:Line one\\nLine two"\r\n', out)
        self.assertIn("visible = true\r\n", out)


class TestPoHarvester(unittest.TestCase):
    PO = (
        'msgid ""\nmsgstr ""\n"Language: tr\\n"\n\n'
        '#, fuzzy\nmsgid "Open"\nmsgstr "Ac"\n\n'
        'msgctxt "door"\nmsgid "Open"\nmsgstr "Kapi ac"\n\n'
        'msgid "%d apple"\nmsgid_plural "%d apples"\nmsgstr[0] "%d elma"\nmsgstr[1] "%d elma"\n\n'
        'msgid "Long "\n"text"\nmsgstr ""\n'
    )

    def test_context_plural_fuzzy_and_continuations(self) -> None:
        entries = TranslationHarvester.harvest_po(self.PO, "res://g.po")
        self.assertEqual([e.source_text for e in entries], ["Open", "Open", "%d apple", "Long text"])
        self.assertTrue(entries[0].metadata["fuzzy"])
        self.assertFalse(entries[1].metadata["fuzzy"])
        self.assertEqual(entries[1].metadata["msgctxt"], "door")
        self.assertNotEqual(entries[0].key, entries[1].key)
        self.assertEqual(entries[2].metadata["msgid_plural"], "%d apples")
        self.assertEqual(entries[2].translation, "%d elma")


class TestDialogueManagerEdgeCases(unittest.TestCase):
    SOURCE = (
        "~ start\r\n"
        "if player.has_key: open the door\r\n"
        "do quest.start(\"a: b\")\r\n"
        "Nathan (happy): Hello {{player.name}}! [ID:HELLO_1] # greeting\r\n"
        "- Sure => next\r\n"
        "=> END\r\n"
    )

    def test_logic_lines_are_not_dialogue(self) -> None:
        entries = DialogueManagerParser.parse_content(self.SOURCE, "res://d.dialogue")
        self.assertEqual([e.source_text for e in entries], ["Hello {{player.name}}!", "Sure"])

    def test_rewrite_preserves_mood_id_tag_and_comment(self) -> None:
        entries = DialogueManagerParser.parse_content(self.SOURCE, "res://d.dialogue")
        out = DialogueManagerParser.apply_translations(
            self.SOURCE, {entries[0].key: "Merhaba {{player.name}}!", entries[1].key: "Tabii"}, "res://d.dialogue"
        )
        self.assertIn("Nathan (happy): Merhaba {{player.name}}! [ID:HELLO_1] # greeting\r\n", out)
        self.assertIn("- Tabii => next\r\n", out)
        self.assertIn("if player.has_key: open the door\r\n", out)

    def test_mustache_expressions_are_protected_as_a_unit(self) -> None:
        protected, tags = UnitySyntaxGuard.protect("Hi {{player.name}}, welcome")
        self.assertEqual(tags, ["{{player.name}}"])
        self.assertNotIn("player", protected)


class TestCustomJsonKeys(unittest.TestCase):
    DATA = '{"cards": [{"id": "c1", "card_description": "Deal 6 damage", "title": "Strike"}], "flavor_text": "Quiet now"}'

    def test_extra_keys_extend_the_built_in_fields(self) -> None:
        self.assertEqual(parse_json_keys("card_description, flavor_text  flavor_text"), ("card_description", "flavor_text"))
        default = DialogicParser.parse_generic_json(self.DATA, "res://c.json")
        self.assertEqual([e.source_text for e in default], ["Strike"])
        keys = parse_json_keys("card_description, flavor_text")
        custom = DialogicParser.parse_generic_json(self.DATA, "res://c.json", keys)
        self.assertEqual({e.source_text for e in custom}, {"Deal 6 damage", "Strike", "Quiet now"})

    def test_rewrite_uses_the_same_keys(self) -> None:
        keys = parse_json_keys("card_description")
        entries = DialogicParser.parse_generic_json(self.DATA, "res://c.json", keys)
        lookup = {e.key: f"T:{e.source_text}" for e in entries}
        out = DialogicParser.apply_generic_json_translations(self.DATA, lookup, "res://c.json", keys)
        self.assertIn("T:Deal 6 damage", out)
        self.assertNotIn("T:Deal 6 damage", DialogicParser.apply_generic_json_translations(self.DATA, lookup, "res://c.json"))


if __name__ == "__main__":
    unittest.main()


class TestGameVariablesInBrackets(unittest.TestCase):
    """Cards in some games say 'Attack for [damage] damage'; the game fills [damage] in at runtime."""

    ORIGINAL = "Attack for [damage] damage. Improve all Law cards by 3 this combat."

    def test_a_translated_variable_is_rejected(self) -> None:
        self.assertEqual(UnitySyntaxGuard.protect(self.ORIGINAL)[1], ["[damage]"])
        ok, errors = UnitySyntaxGuard.validate_integrity(self.ORIGINAL, "[hasar] hasar ver. Bu savaşta kartları 3 artır.")
        self.assertFalse(ok)
        self.assertTrue(any("[damage]" in e for e in errors))

    def test_a_kept_variable_passes_and_a_dropped_one_is_repaired(self) -> None:
        self.assertTrue(UnitySyntaxGuard.validate_integrity(self.ORIGINAL, "[damage] hasar ver. Kartları 3 artır.")[0])
        repaired = UnitySyntaxGuard.repair_missing_tokens(self.ORIGINAL, "Hasar ver. Bu savaşta kartları 3 artır.")
        self.assertIsNotNone(repaired)
        self.assertIn("[damage]", repaired)

    def test_ordinary_bracketed_words_stay_translatable(self) -> None:
        for text in ("[Leave] Walk away from the broken man.", "[Corrupt] Break his faith entirely.", "[b]Bold[/b] text", "[color=red]Red[/color]"):
            tokens = UnitySyntaxGuard.protect(text)[1]
            self.assertNotIn("[Leave]", tokens)
            self.assertNotIn("[Corrupt]", tokens)
        self.assertEqual(UnitySyntaxGuard.protect("[b]Bold[/b]")[1], ["[b]", "[/b]"])  # BBCode tags are still tags
