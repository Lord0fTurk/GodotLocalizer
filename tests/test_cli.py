"""Command-line interface: argument handling, exit codes and the offline round trip."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.cli import main
from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter
from src.core.pipeline.translation_runner import BatchTranslationRunner, TranslationReport
from src.patcher.bootstrap_script import TRANSLATIONS_RES_PATH
from tests.rsrc_builder import menu_scene

KEY = "00112233445566778899aabbccddeeff" * 2


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as exc:  # argparse usage errors
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"GODOTLOCALIZER_DATA_DIR": str(self.root / "data"),
                                                  "GODOTLOCALIZER_NO_KEYRING": "1"})
        self._env.start()
        self.game = self.root / "game"
        self.game.mkdir()
        writer = PCKWriter(self.game / "Game.pck", engine_version=(4, 2, 0))
        writer.add_bytes("res://menu.scn", menu_scene())
        writer.build()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    @staticmethod
    def fake_engine(strings: list[str], **_kwargs: object) -> TranslationReport:
        table = {"Hello World": "Hallo Welt", "Start Game": "Spiel starten", "The game title": "Der Spieltitel"}
        return TranslationReport(translations={s: table[s] for s in strings if s in table})

    def test_usage_errors_exit_with_2(self) -> None:
        self.assertEqual(run_cli()[0], 2)
        self.assertEqual(run_cli("translate", str(self.game))[0], 2)  # --lang is required
        self.assertEqual(run_cli("translate", str(self.game), "--lang", "de", "--engine", "nope")[0], 2)

    def test_inspect_and_status_before_and_after_translating(self) -> None:
        code, out, _ = run_cli("inspect", str(self.game))
        self.assertEqual(code, 0)
        self.assertIn("Godot          : 4.2.0", out)
        self.assertIn("Patch          : no", out)
        self.assertIn("not patched", run_cli("status", str(self.game))[1])

        with mock.patch.object(BatchTranslationRunner, "run", side_effect=lambda **kw: self.fake_engine(kw["strings"])):
            code, out, _ = run_cli("translate", str(self.game), "--lang", "de", "--quiet")
        self.assertEqual(code, 0, out)
        self.assertIn("3/3 strings translated", out)
        patch = PCKReader(self.game / "godotlocalizer_patch.pck")
        patch.open()
        messages = json.loads(patch.read_file_bytes(TRANSLATIONS_RES_PATH))["messages"]
        self.assertEqual(messages["Start Game"], "Spiel starten")
        self.assertIn("patched (de)", run_cli("status", str(self.game))[1])

    def test_export_install_and_revert(self) -> None:
        with mock.patch.object(BatchTranslationRunner, "run", side_effect=lambda **kw: self.fake_engine(kw["strings"])):
            run_cli("translate", str(self.game), "--lang", "de", "--quiet")
        archive = self.root / "share" / "patch.zip"
        self.assertEqual(run_cli("export", str(self.game), "-o", str(archive))[0], 0)
        self.assertTrue(archive.is_file())

        self.assertEqual(run_cli("revert", str(self.game))[0], 0)
        self.assertIn("not patched", run_cli("status", str(self.game))[1])
        code, out, _ = run_cli("install", str(archive), str(self.game))
        self.assertEqual((code, out.strip()), (0, "installed (de)"))
        self.assertIn("patched (de)", run_cli("status", str(self.game))[1])

    def test_nothing_to_translate_and_missing_key_have_their_own_exit_codes(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        writer = PCKWriter(empty / "Game.pck")
        writer.add_bytes("res://readme.txt", b"hi")
        writer.build()
        code, _, err = run_cli("translate", str(empty), "--lang", "de", "--quiet")
        self.assertEqual(code, 3)
        self.assertIn("No translatable text", err)

        locked = self.root / "locked"
        locked.mkdir()
        writer = PCKWriter(locked / "Game.pck", encryption_key=KEY, encrypt_directory=True)
        writer.add_bytes("res://a.txt", b"hello", encrypted=True)
        writer.build()
        self.assertEqual(run_cli("inspect", str(locked))[0], 4)
        self.assertEqual(run_cli("inspect", str(locked), "--pck-key", KEY)[0], 0)

    def test_missing_game_is_a_plain_error(self) -> None:
        code, _, err = run_cli("inspect", str(self.root / "nowhere"))
        self.assertEqual(code, 1)
        self.assertIn("Error:", err)


if __name__ == "__main__":
    unittest.main()
