"""Tests for override.cfg safety, runtime translations, write-back, pack discovery and syntax guard."""

import json
import struct
import tempfile
import unittest
from pathlib import Path

from src.core.pck.constants import PCK_MAGIC
from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter
from src.core.pipeline.harvest import build_localized_files, build_runtime_messages, harvest_pack, is_translation_key
from src.core.pipeline.orchestrator import GodotTranslationPipeline
from src.core.syntax_guard import UnitySyntaxGuard
from src.harvesters.base import StringSourceType
from src.harvesters.scene_harvester import SceneHarvester
from src.parsers.dialogic import DialogicParser
from src.patcher.bootstrap_script import TRANSLATIONS_RES_PATH, godot_locale
from src.patcher.overlay_patcher import OverlayPatcher
from src.patcher.override_cfg import add_autoload, remove_autoload


class TestOverrideCfg(unittest.TestCase):
    def test_merge_into_crlf_file_keeps_existing_entries(self) -> None:
        original = '[autoload]\r\nGameStuff="*res://game.gd"\r\n\r\n[display]\r\nwindow/size/width=1280\r\n'
        merged = add_autoload(original, "res://b.gd", prepend=False)
        self.assertIn('GodotLocalizerBootstrap="*res://b.gd"', merged)
        self.assertIn('GameStuff="*res://game.gd"', merged)
        self.assertIn("window/size/width=1280", merged)
        self.assertNotIn("\r", merged)

    def test_prepend_section_written_alongside_autoload(self) -> None:
        merged = add_autoload("", "res://b.gd", prepend=True)
        self.assertIn("[autoload_prepend]", merged)
        self.assertIn("[autoload]", merged)

    def test_idempotent_and_removable(self) -> None:
        once = add_autoload("", "res://b.gd", prepend=True)
        twice = add_autoload(once, "res://b.gd", prepend=True)
        self.assertEqual(once, twice)
        self.assertEqual(remove_autoload(twice), "")

    def test_remove_keeps_foreign_settings(self) -> None:
        cfg = add_autoload("[display]\nwindow/size/width=1280\n", "res://b.gd", prepend=True)
        self.assertEqual(remove_autoload(cfg), "[display]\nwindow/size/width=1280\n")


class TestOverlayDeployment(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_remove_preserves_games_own_override_cfg(self) -> None:
        cfg = self.dir / "override.cfg"
        cfg.write_bytes(b"[display]\r\nwindow/size/width=1280\r\n")
        patcher = OverlayPatcher(self.dir)
        patcher.build_overlay_patch({}, target_locale="tr")
        self.assertIn("GodotLocalizerBootstrap", cfg.read_text(encoding="utf-8"))
        patcher.remove_patch()
        self.assertTrue(cfg.exists())
        self.assertEqual(cfg.read_text(encoding="utf-8"), "[display]\nwindow/size/width=1280\n")
        self.assertFalse((self.dir / OverlayPatcher.DEFAULT_PATCH_PCK).exists())

    def test_game_owned_patch_pck_is_never_touched(self) -> None:
        theirs = self.dir / "patch.pck"
        theirs.write_bytes(b"GAME DLC")
        patcher = OverlayPatcher(self.dir)
        patcher.build_overlay_patch({}, target_locale="tr")
        patcher.remove_patch()
        self.assertEqual(theirs.read_bytes(), b"GAME DLC")

    def test_runtime_translation_table_is_packed(self) -> None:
        patcher = OverlayPatcher(self.dir)
        patcher.build_overlay_patch({}, target_locale="pt-BR", runtime_translations={"Start": "Iniciar"})
        reader = PCKReader(self.dir / OverlayPatcher.DEFAULT_PATCH_PCK)
        reader.open()
        data = json.loads(reader.read_file_bytes(TRANSLATIONS_RES_PATH))
        self.assertEqual(data, {"locale": "pt_BR", "messages": {"Start": "Iniciar"}})

    def test_failed_build_rolls_back(self) -> None:
        patcher = OverlayPatcher(self.dir)
        with self.assertRaises(TypeError):
            patcher.build_overlay_patch({"res://a.txt": "not bytes"}, target_locale="tr")  # type: ignore[dict-item]
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_godot_locale_normalization(self) -> None:
        self.assertEqual(godot_locale("pt-br"), "pt_BR")
        self.assertEqual(godot_locale("zh-Hans-CN"), "zh_Hans_CN")
        self.assertEqual(godot_locale('tr"; evil'), "trevil")
        self.assertEqual(godot_locale(""), "en")


class TestWriteBack(unittest.TestCase):
    def test_tscn_roundtrip_preserves_crlf_and_other_lines(self) -> None:
        src = '[node name="L" type="Label"]\r\ntext = "Play"\r\nvisible = true\r\n'
        entries = SceneHarvester.harvest_tscn(src, "res://a.tscn")
        out = SceneHarvester.apply_tscn_translations(src, {entries[0].key: 'Oyna "şimdi"'}, "res://a.tscn")
        self.assertEqual(out, '[node name="L" type="Label"]\r\ntext = "Oyna \\"şimdi\\""\r\nvisible = true\r\n')

    def test_dtl_roundtrip(self) -> None:
        src = "Nathan: Hello there\n- Yes please\n"
        entries = DialogicParser.parse_dtl(src, "res://t.dtl")
        lookup = {entries[0].key: "Merhaba", entries[1].key: "Evet lütfen"}
        out = DialogicParser.apply_dtl_translations(src, lookup, "res://t.dtl")
        self.assertEqual(out, "Nathan: Merhaba\n- Evet lütfen\n")

    def test_d1_json_roundtrip(self) -> None:
        src = json.dumps({"events": [{"event_id": "dialogic_001", "text": "Hi", "character": "A"}]})
        entry = DialogicParser.parse_d1_json(src, "res://timelines/a.json")[0]
        out = DialogicParser.apply_d1_json_translations(src, {entry.key: "Selam"}, "res://timelines/a.json")
        self.assertEqual(json.loads(out)["events"][0]["text"], "Selam")


class TestHarvestPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        writer = PCKWriter(self.dir / "game.pck", engine_version=(4, 2, 0))
        writer.add_bytes("res://ui.tscn", b'[node name="B" type="Button"]\ntext = "Start"\ntooltip_text = "UI_START_TIP"\n')
        writer.add_bytes("res://tex.png", b"\x89PNG" + b"\x00" * 64)
        writer.add_bytes("res://t.csv", b"id,en\nMENU_EXIT,Exit Game\n")
        writer.build()
        self.reader = PCKReader(self.dir / "game.pck")
        self.reader.open()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_binary_assets_are_never_read(self) -> None:
        reads: list[str] = []
        original = self.reader.read_file_bytes
        self.reader.read_file_bytes = lambda p, **kw: (reads.append(p), original(p, **kw))[1]  # type: ignore[method-assign]
        harvest_pack(self.reader)
        self.assertNotIn("res://tex.png", reads)

    def test_translation_keys_are_not_harvested(self) -> None:
        texts = [e.source_text for e in harvest_pack(self.reader)]
        self.assertIn("Start", texts)
        self.assertNotIn("UI_START_TIP", texts)
        self.assertTrue(is_translation_key("menu.start_game".upper()))
        self.assertFalse(is_translation_key("OK"))

    def test_scene_and_runtime_messages(self) -> None:
        entries = harvest_pack(self.reader)
        for e in entries:
            e.translation = {"Start": "Başla", "Exit Game": "Çıkış"}.get(e.source_text, "")
        files = build_localized_files(self.reader, entries)
        self.assertIn(b'text = "Ba\xc5\x9fla"', files["res://ui.tscn"])
        self.assertNotIn("res://t.csv", files)  # CSV is an import source, not read at runtime
        messages = build_runtime_messages(entries)
        self.assertEqual(messages["Start"], "Başla")
        self.assertEqual(messages["MENU_EXIT"], "Çıkış")  # key-based lookup for CSV games
        csv_entry = next(e for e in entries if e.source_type is StringSourceType.TRANSLATION)
        self.assertEqual(csv_entry.metadata["translation_key"], "MENU_EXIT")

    def test_pick_game_file_ignores_own_output_and_helpers(self) -> None:
        (self.dir / OverlayPatcher.DEFAULT_PATCH_PCK).write_bytes(b"x" * 10_000_000)
        (self.dir / "launcher.exe").write_bytes(b"MZ" + b"\x00" * 100)
        pipeline = GodotTranslationPipeline.__new__(GodotTranslationPipeline)
        pipeline.pck_key = ""
        picked = pipeline._pick_game_file(self.dir)
        self.assertEqual(picked.name, "game.pck")


class TestReaderExtras(unittest.TestCase):
    def test_pe_pck_section_is_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pck_path = Path(tmp) / "inner.pck"
            writer = PCKWriter(pck_path, engine_version=(4, 3, 0))
            writer.add_bytes("res://a.txt", b"data")
            writer.build()
            pck = pck_path.read_bytes()

            pe_offset, opt_size = 0x80, 0xE0
            raw_ptr = 0x400
            dos = b"MZ" + b"\x00" * 0x3A + struct.pack("<I", pe_offset)
            coff = b"PE\x00\x00" + struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, opt_size, 0x22)
            section = b"pck\x00\x00\x00\x00\x00" + struct.pack("<IIIIIIHHI", len(pck), 0x1000, len(pck), raw_ptr, 0, 0, 0, 0, 0x40000040)
            blob = dos.ljust(pe_offset, b"\x00") + coff + b"\x00" * opt_size + section
            exe = Path(tmp) / "Game.exe"
            exe.write_bytes(blob.ljust(raw_ptr, b"\x00") + pck)

            reader = PCKReader(exe)
            reader.open()
            self.assertTrue(reader.is_embedded)
            self.assertEqual(reader.pck_offset, raw_ptr)
            self.assertEqual(reader.read_file_bytes("res://a.txt", verify_md5=True), b"data")
            self.assertEqual(exe.read_bytes()[raw_ptr:raw_ptr + 4], PCK_MAGIC)

    def test_md5_mismatch_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "g.pck"
            writer = PCKWriter(path)
            writer.add_bytes("res://a.txt", b"original")
            writer.build()
            reader = PCKReader(path)
            reader.open()
            entry = reader.entries["res://a.txt"]
            raw = bytearray(path.read_bytes())
            raw[entry.offset] ^= 0xFF
            path.write_bytes(bytes(raw))
            self.assertIsNone(reader.read_file_bytes("res://a.txt", verify_md5=True))
            self.assertFalse(reader.extract_file("res://a.txt", Path(tmp) / "out.txt"))
            self.assertFalse((Path(tmp) / "out.txt").exists())


class TestGodotSyntaxGuard(unittest.TestCase):
    def test_bbcode_and_format_tokens_must_survive(self) -> None:
        original = "[color=red]Score:[/color] %05d [wave amp=50]go[/wave] 100%%"
        self.assertTrue(UnitySyntaxGuard.validate_integrity(original, original)[0])
        broken = "Skor: %05d [wave amp=50]git[/wave] 100%%"
        ok, errors = UnitySyntaxGuard.validate_integrity(original, broken)
        self.assertFalse(ok)
        self.assertTrue(any("[color=red]" in e for e in errors))

    def test_plain_percent_prose_is_not_a_token(self) -> None:
        self.assertTrue(UnitySyntaxGuard.validate_integrity("50% off today", "Bugün %50 indirim")[0])

    def test_paired_godot_tags_are_not_blindly_repaired(self) -> None:
        self.assertIsNone(UnitySyntaxGuard.repair_missing_tokens("[center]Hi[/center]", "Selam"))


if __name__ == "__main__":
    unittest.main()


class TestLegacyUpgrade(unittest.TestCase):
    """Folders patched by the first versions (patch.pck + patch_bootstrap.gd) are upgraded or cleaned safely."""

    OLD_SCRIPT = (
        "# GodotLocalizer Runtime Bootstrap (Godot 4.x)\nextends Node\n"
        'func _enter_tree() -> void:\n    var p = exe_dir.path_join("patch.pck")\n'
    )

    def make_old_install(self, root: Path) -> None:
        (root / "patch_bootstrap.gd").write_text(self.OLD_SCRIPT, encoding="utf-8")
        (root / "patch.pck").write_bytes(b"old")
        (root / "override.cfg").write_text('[autoload]\nGodotLocalizerBootstrap="*res://patch_bootstrap.gd"\n', encoding="utf-8")

    def test_revert_removes_the_old_files_and_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_old_install(root)
            OverlayPatcher(root).remove_patch()
            self.assertEqual(sorted(p.name for p in root.iterdir()), [])

    def test_new_deployment_replaces_the_old_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_old_install(root)
            OverlayPatcher(root).build_overlay_patch({}, target_locale="tr", runtime_translations={"A": "B"})
            names = {p.name for p in root.iterdir()}
            self.assertNotIn("patch_bootstrap.gd", names)
            self.assertNotIn("patch.pck", names)
            cfg = (root / "override.cfg").read_text(encoding="utf-8")
            self.assertIn("godotlocalizer_bootstrap.gd", cfg)
            self.assertNotIn("patch_bootstrap.gd", cfg)

    def test_a_games_own_patch_pck_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "patch.pck").write_bytes(b"game dlc")
            OverlayPatcher(root).remove_patch()
            self.assertTrue((root / "patch.pck").exists())
