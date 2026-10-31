"""Game-open problems reach the user as localized text (dialog), not as a raw English exception line."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QCoreApplication

from src.backend.app_backend import AppBackend
from src.backend.errors import describe_error
from src.backend.locale_manager import LocaleManager
from src.core.pck.constants import PCK_MAGIC
from src.core.pck.reader import CustomPckSignatureError
from src.core.pipeline.orchestrator import NoGamePackageError
from src.utils.config import ConfigManager
from tests.test_linux_support import build_elf, build_pck


class TestDescribeError(unittest.TestCase):
    def test_known_errors_get_their_own_keys(self) -> None:
        info = describe_error(CustomPckSignatureError("Game.exe", b"KCVK"))
        self.assertEqual((info.key, info.blocking), ("errors.custom_signature", True))
        self.assertEqual(info.params, {"signature": "KCVK", "file": "Game.exe"})
        info = describe_error(NoGamePackageError(Path("/games/x")))
        self.assertEqual((info.key, info.blocking), ("errors.no_game_package", True))

    def test_unknown_errors_keep_the_generic_localized_prefix(self) -> None:
        info = describe_error(OSError("disk full"))
        self.assertEqual((info.key, info.blocking), ("dashboard.status_error", False))
        self.assertEqual(info.params, {"error": "disk full"})


class TestBackendLocalizesTheDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        with mock.patch("src.backend.app_backend.ConfigManager", lambda: ConfigManager(self.dir / "c.json", vault=None)):
            self.backend = AppBackend()
        self.dialogs: list[tuple[str, str]] = []
        self.toasts: list[tuple[str, str]] = []
        self.backend.blockingError.connect(lambda title, text: self.dialogs.append((title, text)))
        self.backend.toastRequested.connect(lambda text, kind: self.toasts.append((text, kind)))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_custom_signature_game_opens_a_turkish_dialog_without_a_toast(self) -> None:
        LocaleManager(default_lang="tr")
        pck = build_pck(self.dir / "inner.pck").replace(PCK_MAGIC, b"KCVK", 1)
        (self.dir / "inner.pck").unlink()
        (self.dir / "Game.x86_64").write_bytes(build_elf(pck))
        self.backend.selectGame(str(self.dir))
        self.assertEqual(len(self.dialogs), 1)
        title, text = self.dialogs[0]
        self.assertEqual(title, "Bu oyun açılamıyor")
        self.assertIn("KCVK", text)
        self.assertIn("özel bir Godot derlemesiyle", text)
        self.assertNotIn("custom Godot build", text)
        self.assertEqual(self.toasts, [])
        self.assertEqual(self.backend.statusText, "Oyun açılamadı")  # short, so it fits the dashboard status line

    def test_empty_folder_explains_what_to_select(self) -> None:
        LocaleManager(default_lang="en")
        self.backend.selectGame(str(self.dir))
        self.assertEqual(len(self.dialogs), 1)
        self.assertIn("Select the game's .pck file", self.dialogs[0][1])

    def test_ordinary_failures_still_use_a_toast(self) -> None:
        LocaleManager(default_lang="en")
        self.backend.selectGame(str(self.dir / "missing.pck"))
        self.assertEqual(self.dialogs, [])
        self.assertEqual(self.toasts[-1][1], "error")


if __name__ == "__main__":
    unittest.main()
