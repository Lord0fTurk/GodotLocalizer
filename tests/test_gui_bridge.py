"""Unit tests for GUI bridge and internationalization (i18n) manager."""

import tempfile
import unittest
from pathlib import Path

from PyQt6.QtCore import QCoreApplication

from src.core.pck.constants import PCK_FORMAT_VERSION_2
from src.core.pck.writer import PCKWriter
from src.gui.bridge import AppBridge
from src.gui.i18n.manager import I18nManager
from src.utils.config import ConfigManager


class TestGUIBridgeAndI18n(unittest.TestCase):
    """Verifies i18n catalogs and backend bridge slots."""

    @classmethod
    def setUpClass(cls) -> None:
        if not QCoreApplication.instance():
            cls.app = QCoreApplication([])
        else:
            cls.app = QCoreApplication.instance()

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_i18n_manager(self) -> None:
        """Verifies multi-language catalog loading and fallback behavior."""
        i18n = I18nManager()
        self.assertIn("tr", i18n._catalogs)
        self.assertIn("en", i18n._catalogs)
        self.assertIn("de", i18n._catalogs)

        i18n.set_locale("en")
        self.assertEqual(i18n.t("nav.dashboard"), "Dashboard")

        i18n.set_locale("tr")
        self.assertEqual(i18n.t("nav.dashboard"), "Genel Bakış")

        # Test fallback on missing key in another language
        i18n.set_locale("de")
        self.assertEqual(i18n.t("nav.dashboard"), "Übersicht")

    def test_bridge_open_game_and_harvest(self) -> None:
        """Tests opening a PCK and harvesting strings via AppBridge."""
        # 1. Build a mock PCK with dialogue and scene data
        mock_pck = self.base_path / "test_game.pck"
        writer = PCKWriter(
            output_path=mock_pck,
            format_version=PCK_FORMAT_VERSION_2,
            engine_version=(4, 2, 1),
        )
        writer.add_bytes(
            "res://scenes/hud.tscn",
            b'[node name="Health" type="Label"]\ntext = "Health: 100%"\n',
        )
        writer.add_bytes(
            "res://dialogues/npc.dialogue",
            b'~ start\nGuard: Halt! Who goes there?\n- A humble traveler => pass\n',
        )
        writer.build()

        # 2. Initialize AppBridge
        cfg = ConfigManager(self.base_path / "test_config.json")
        bridge = AppBridge(config_manager=cfg)

        # 3. Open Game
        success = bridge.openGame(str(mock_pck))
        self.assertTrue(success)
        self.assertEqual(bridge.packFormat, 2)
        self.assertEqual(bridge.engineVersion, "4.2.1")
        self.assertEqual(bridge.fileCount, 2)
        self.assertFalse(bridge.isEmbedded)

        # 4. Harvest strings
        count = bridge.harvestStrings()
        self.assertEqual(count, 3)
        self.assertEqual(bridge.harvestedCount, 3)

        preview = bridge.getHarvestedPreview()
        self.assertEqual(len(preview), 3)

        # 5. Theme and locale changes
        bridge.setTheme("cyber_neon")
        self.assertEqual(bridge.currentTheme, "cyber_neon")

        bridge.setLocale("en")
        self.assertEqual(bridge.currentLocale, "en")


if __name__ == "__main__":
    unittest.main()
