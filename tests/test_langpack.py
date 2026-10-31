"""Games with their own external language packs (external/data + external/mods/lang_*)."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.cli import main as cli_main
from src.core import langpack as lp
from src.core.pck.writer import PCKWriter
from src.core.pipeline.orchestrator import GodotTranslationPipeline
from src.core.pipeline.translation_runner import TranslationReport
from src.patcher.package import patch_status

CARD = {"patch_data": {}, "properties": {
    "object_id": "card_a", "card_name": "Blade", "card_description": "Deal [damage] damage.",
    "card_texture_path": "external/sprites/blade.png", "card_energy_cost": 1,
    "card_first_upgrade_property_changes": {"card_description": "Deal [damage] damage twice.", "card_energy_cost": 0},
}}
DIALOGUE = {"patch_data": {}, "properties": {
    "object_id": "dlg", "dialogue_initial_dialogue_state_object_id": "intro",
    "dialogue_state_id_to_dialogue_states": {"intro": {"object_id": "intro", "dialogue_state_prompt_bbcode": "An old altar."}},
    "dialogue_option_id_to_dialogue_options": {"pray": {
        "object_id": "pray", "dialogue_option_bbcode": "[Pray] Kneel.",
        "dialogue_option_actions": [{"res://a.gd": {"card_pick_text": "Pick one.", "amount": 3}}],
    }},
}}


def write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent="\t"), encoding="utf-8")


def make_game(folder: Path) -> None:
    ext = folder / "external"
    write(ext / "data/cards/card_a.json", CARD)
    write(ext / "data/dialogue/dlg.json", DIALOGUE)
    de = ext / "mods/lang_de"
    write(de / "ui_strings.json", {"Back": "Zurück", "Settings": "Einstellungen"})
    write(de / "mod_info.json", {"patch_data": {}, "properties": {
        "mod_name": "Language (de)", "mod_description": "Language pack: de.", "object_id": "mod_data_lang_de",
        "mod_folder_to_load_data": {"external/mods/lang_de/cards/": {"class_name": "CardData"}},
    }})
    write(de / "cards/card_a.json", {"patch_data": {}, "properties": {
        "object_id": "card_a", "card_name": "Klinge", "card_description": "Verursache [damage] Schaden.",
        "card_first_upgrade_property_changes": {"card_description": "Verursache [damage] Schaden zweimal.", "card_energy_cost": 0},
    }})
    write(de / "dialogue/dlg.json", {"patch_data": {}, "properties": {
        "object_id": "dlg",
        "dialogue_state_id_to_dialogue_states": {"intro": {"object_id": "intro", "dialogue_state_prompt_bbcode": "Ein alter Altar."}},
        "dialogue_option_id_to_dialogue_options": {"pray": {
            "object_id": "pray", "dialogue_option_bbcode": "[Beten] Knie nieder.",
            "dialogue_option_actions": [{"res://a.gd": {"card_pick_text": "Wähle eine.", "amount": 3}}],
        }},
    }})
    writer = PCKWriter(folder / "Game.pck", engine_version=(4, 6, 0))
    writer.add_bytes("res://x.txt", b"x")
    writer.build()


class TestLanguagePack(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        make_game(self.dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_games_without_an_official_pack_are_not_detected(self) -> None:
        (self.dir / "external/mods/lang_de/ui_strings.json").unlink()
        self.assertIsNone(lp.find_language_root(self.dir))

    def test_schema_is_learned_from_the_official_pack(self) -> None:
        schema = lp.learn_schema(lp.find_language_root(self.dir))
        self.assertEqual(schema.references, ["de"])
        self.assertTrue({"card_name", "card_description", "dialogue_option_bbcode", "card_pick_text"} <= schema.text_keys)
        self.assertNotIn("object_id", schema.text_keys)
        self.assertNotIn("card_texture_path", schema.properties["cards"])  # packs do not carry it
        self.assertEqual(schema.ui_keys, ["Back", "Settings"])

    def test_harvest_finds_text_only(self) -> None:
        strings = lp.harvest_strings(lp.learn_schema(lp.find_language_root(self.dir)))
        self.assertIn("Deal [damage] damage.", strings)
        self.assertIn("Pick one.", strings)
        self.assertIn("Back", strings)
        self.assertNotIn("external/sprites/blade.png", strings)
        self.assertNotIn("card_a", strings)

    def test_pack_has_the_shape_of_the_official_one(self) -> None:
        root = lp.find_language_root(self.dir)
        schema = lp.learn_schema(root)
        count = lp.build_pack(schema, {"Blade": "Kılıç", "Back": "Geri", "Pick one.": "Birini seç."}, "tr")
        pack = root / "mods/lang_tr"
        card = json.loads((pack / "cards/card_a.json").read_text(encoding="utf-8"))["properties"]
        self.assertEqual(card["card_name"], "Kılıç")
        self.assertEqual(card["card_description"], "Deal [damage] damage.")  # untranslated stays English
        self.assertEqual(card["card_first_upgrade_property_changes"]["card_energy_cost"], 0)
        self.assertNotIn("card_texture_path", card)
        dialogue = json.loads((pack / "dialogue/dlg.json").read_text(encoding="utf-8"))["properties"]
        action = dialogue["dialogue_option_id_to_dialogue_options"]["pray"]["dialogue_option_actions"][0]["res://a.gd"]
        self.assertEqual(action, {"card_pick_text": "Birini seç.", "amount": 3})
        self.assertEqual(json.loads((pack / "ui_strings.json").read_text(encoding="utf-8"))["Back"], "Geri")
        info = (pack / "mod_info.json").read_text(encoding="utf-8")
        self.assertIn("external/mods/lang_tr/cards/", info)
        self.assertNotIn("lang_de", info)
        self.assertEqual(count, 3)

    def test_official_packs_are_never_replaced(self) -> None:
        schema = lp.learn_schema(lp.find_language_root(self.dir))
        with self.assertRaises(lp.LangPackError):
            lp.build_pack(schema, {}, "de")

    def test_whitespace_around_text_survives(self) -> None:
        self.assertEqual(lp._wrap("  Hi\n", {"Hi": "Selam"}), "  Selam\n")

    def test_status_and_removal(self) -> None:
        schema = lp.learn_schema(lp.find_language_root(self.dir))
        lp.build_pack(schema, {"Blade": "Kılıç"}, "pt-BR")
        self.assertTrue((self.dir / "external/mods/lang_pt_br").is_dir())
        status = patch_status(self.dir)
        self.assertTrue(status.patched)
        self.assertEqual(status.locale, "pt-BR")
        self.assertEqual(lp.remove_packs(self.dir), 1)
        self.assertTrue((self.dir / "external/mods/lang_de").is_dir())  # official pack untouched
        self.assertFalse(patch_status(self.dir).patched)


class TestLanguagePackPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        make_game(self.dir)
        self.pipeline = GodotTranslationPipeline.__new__(GodotTranslationPipeline)
        self.pipeline.pck_key = ""
        self.pipeline.gdre_path = ""
        self.pipeline.json_text_keys = ""
        self.pipeline.last_harvest_count = 0
        self.pipeline.progress_callback = None
        self.pipeline.last_session = None

        def fake_run(strings: list[str], **_kw: object) -> TranslationReport:
            return TranslationReport(translations={s: f"TR {s}" for s in strings if "[" not in s})

        self.pipeline.runner = SimpleNamespace(run=fake_run)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_run_writes_a_pack_instead_of_an_overlay(self) -> None:
        session = self.pipeline.run(self.dir, "tr")
        self.assertGreater(session.shippable_count, 0)
        card = json.loads((self.dir / "external/mods/lang_tr/cards/card_a.json").read_text(encoding="utf-8"))
        self.assertEqual(card["properties"]["card_name"], "TR Blade")
        self.assertFalse((self.dir / "godotlocalizer_patch.pck").exists())
        self.assertFalse((self.dir / "override.cfg").exists())

    def test_reviewed_edits_rebuild_the_pack(self) -> None:
        session = self.pipeline.run(self.dir, "tr")
        row = next(r for r in session.rows if r.original == "Blade")
        session.edit(row.row_id, "Kılıç")
        self.pipeline.apply_session(session)
        card = json.loads((self.dir / "external/mods/lang_tr/cards/card_a.json").read_text(encoding="utf-8"))
        self.assertEqual(card["properties"]["card_name"], "Kılıç")

    def test_cli_revert_removes_the_pack(self) -> None:
        self.pipeline.run(self.dir, "tr")
        self.assertEqual(cli_main(["revert", str(self.dir)]), 0)
        self.assertFalse((self.dir / "external/mods/lang_tr").exists())


if __name__ == "__main__":
    unittest.main()
