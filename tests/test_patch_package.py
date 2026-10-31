"""Patch status, shareable patch packages and applying a reviewed session (offline)."""

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter
from src.core.pipeline.orchestrator import GodotTranslationPipeline
from src.core.pipeline.review_session import ReviewSession, RunContext
from src.patcher.bootstrap_script import TRANSLATIONS_RES_PATH
from src.patcher.overlay_patcher import OverlayPatcher
from src.patcher.package import (
    SIDECAR_NAME,
    export_package,
    install_package,
    patch_status,
    read_sidecar,
    write_sidecar,
)
from tests.rsrc_builder import menu_scene


def make_game(folder: Path, extra: bytes = b"") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    writer = PCKWriter(folder / "Game.pck", engine_version=(4, 2, 0))
    writer.add_bytes("res://menu.scn", menu_scene())
    if extra:
        writer.add_bytes("res://extra.bin", extra)
    writer.build()
    return folder / "Game.pck"


def patch_game(folder: Path, pack: Path, locale: str = "de") -> None:
    OverlayPatcher(folder).build_overlay_patch({}, target_locale=locale, runtime_translations={"Start Game": "Spiel starten"})
    write_sidecar(folder, locale, True, (4, 2, 0), pack)


class TestStatusAndPackage(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.src = self.root / "src_game"
        self.pack = make_game(self.src)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_status_follows_the_patch_and_detects_game_updates(self) -> None:
        self.assertFalse(patch_status(self.src, self.pack).patched)
        patch_game(self.src, self.pack)
        status = patch_status(self.src, self.pack)
        self.assertEqual((status.patched, status.locale, status.game_updated), (True, "de", False))
        make_game(self.src, extra=b"new content in the game update")  # the game was updated afterwards
        self.assertTrue(patch_status(self.src, self.pack).game_updated)
        OverlayPatcher(self.src).remove_patch()
        self.assertFalse(patch_status(self.src, self.pack).patched)
        self.assertIsNone(read_sidecar(self.src))

    def test_export_and_install_roundtrip_keeps_the_recipients_override_cfg(self) -> None:
        patch_game(self.src, self.pack)
        archive = export_package(self.src, self.root / "out" / "p.zip", "Game")
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
        expected = {
            "godotlocalizer_patch.pck", "godotlocalizer_bootstrap.gd", SIDECAR_NAME, "install.bat", "install.sh",
            "uninstall.bat", "uninstall.sh", "README.txt", "override.cfg.godotlocalizer",
        }
        self.assertTrue(expected <= names)
        self.assertNotIn("Game.pck", names)  # never ships game files

        dest = self.root / "dest_game"
        make_game(dest)
        (dest / "override.cfg").write_text("[display]\nwindow/size/width=1280\n", encoding="utf-8")
        result = install_package(archive, dest, dest / "Game.pck")
        self.assertEqual((result.locale, result.warnings), ("de", []))
        cfg = (dest / "override.cfg").read_text(encoding="utf-8")
        self.assertIn("window/size/width=1280", cfg)
        self.assertIn("GodotLocalizerBootstrap", cfg)
        patch = PCKReader(dest / "godotlocalizer_patch.pck")
        patch.open()
        messages = json.loads(patch.read_file_bytes(TRANSLATIONS_RES_PATH))["messages"]
        self.assertEqual(messages, {"Start Game": "Spiel starten"})
        self.assertTrue(patch_status(dest, dest / "Game.pck").patched)

    def test_install_warns_when_the_game_version_differs(self) -> None:
        patch_game(self.src, self.pack)
        archive = export_package(self.src, self.root / "p.zip")
        other = self.root / "other"
        make_game(other, extra=b"another build")
        warnings = install_package(archive, other, other / "Game.pck").warnings
        self.assertTrue(any("differs" in w for w in warnings))

    def test_export_without_a_patch_and_hostile_archives_are_rejected(self) -> None:
        with self.assertRaises(FileNotFoundError):
            export_package(self.src, self.root / "x.zip")
        evil = self.root / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("godotlocalizer_patch.pck", b"x")
            zf.writestr("godotlocalizer_bootstrap.gd", b"x")
            zf.writestr("../escape.txt", b"x")
        with self.assertRaises(ValueError):
            install_package(evil, self.src)
        bad = self.root / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("readme.txt", b"x")
        with self.assertRaises(ValueError):
            install_package(bad, self.src)

    @unittest.skipUnless(sys.platform == "win32", "runs the Windows scripts")
    def test_windows_scripts_install_and_uninstall(self) -> None:
        patch_game(self.src, self.pack)
        archive = export_package(self.src, self.root / "p.zip", "Game")
        dest = self.root / "recipient"
        make_game(dest)
        (dest / "override.cfg").write_text("[display]\nwindow/size/width=1280\n", encoding="utf-8")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)

        def run(script: str) -> None:
            subprocess.run(["cmd", "/c", "echo.| .\\" + script], cwd=dest, capture_output=True, timeout=60, check=True)

        def cfg() -> str:
            return (dest / "override.cfg").read_text(encoding="utf-8")

        run("install.bat")
        self.assertIn("window/size/width=1280", cfg())
        self.assertIn('GodotLocalizerBootstrap="*res://godotlocalizer_bootstrap.gd"', cfg())
        installed = cfg().count("GodotLocalizerBootstrap")
        run("install.bat")  # idempotent
        self.assertEqual(cfg().count("GodotLocalizerBootstrap"), installed)
        run("uninstall.bat")
        self.assertNotIn("GodotLocalizerBootstrap", cfg())
        self.assertIn("window/size/width=1280", cfg())
        self.assertFalse((dest / "godotlocalizer_patch.pck").exists())


class TestApplySession(unittest.TestCase):
    def test_reviewed_edits_reach_the_patch_without_a_translation_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            make_game(folder)
            pipeline = GodotTranslationPipeline.__new__(GodotTranslationPipeline)
            pipeline.pck_key = pipeline.font_path = pipeline.gdre_path = pipeline.json_text_keys = ""
            pipeline.rtl_shaping, pipeline.last_harvest_count, pipeline.last_game_path = True, 0, ""
            session = ReviewSession(context=RunContext(game_root=folder, target_lang="tr"))
            session.add_row("k1", "Hello World", "Merhaba Dünya")
            session.add_row("k2", "Start Game", "Oyunu Başlat")
            session.edit(1, "Başla")  # the translator corrects one row

            self.assertEqual(pipeline.apply_session(session), 2)
            patch = PCKReader(folder / "godotlocalizer_patch.pck")
            patch.open()
            messages = json.loads(patch.read_file_bytes(TRANSLATIONS_RES_PATH))["messages"]
            self.assertEqual(messages, {"Hello World": "Merhaba Dünya", "Start Game": "Başla"})
            self.assertTrue(patch_status(folder).patched)


if __name__ == "__main__":
    unittest.main()
