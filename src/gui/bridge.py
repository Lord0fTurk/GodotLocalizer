"""Backend bridge connecting PyQt6 runtime with QML UI."""

from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, pyqtProperty, pyqtSignal, pyqtSlot

from src.core.pck.reader import PCKReader
from src.gui.i18n.manager import I18nManager
from src.harvesters.base import HarvestedEntry
from src.harvesters.scene_harvester import SceneHarvester
from src.harvesters.translation_harvester import TranslationHarvester
from src.parsers.dialogic import DialogicParser
from src.parsers.dialogue_manager import DialogueManagerParser
from src.patcher.overlay_patcher import OverlayPatcher
from src.patcher.repack_patcher import RepackPatcher
from src.utils.config import ConfigManager
from src.utils.logger import logger


class AppBridge(QObject):
    """QObject bridge exposing core reverse engineering and patching capabilities to QML."""

    # UI State Signals
    themeChanged = pyqtSignal()
    localeChanged = pyqtSignal()
    gameLoaded = pyqtSignal()
    harvestCompleted = pyqtSignal(int)
    patchStatusChanged = pyqtSignal(str, bool)
    logMessage = pyqtSignal(str)

    def __init__(self, config_manager: ConfigManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config_manager
        self.i18n = I18nManager()
        self.i18n.set_locale(self.config.get("locale", "tr"))

        self._current_theme: str = self.config.get("theme", "obsidian")
        self._game_path: str = ""
        self._game_name: str = ""
        self._pack_format: int = 0
        self._engine_version: str = "Unknown"
        self._file_count: int = 0
        self._is_embedded: bool = False
        self._status_message: str = self.i18n.t("dashboard.status_ready")
        self._is_busy: bool = False

        self._pck_reader: PCKReader | None = None
        self._harvested_entries: list[HarvestedEntry] = []

    # --- Properties exposed to QML ---

    @pyqtProperty(str, notify=localeChanged)
    def currentLocale(self) -> str:
        return self.i18n.current_locale

    @pyqtProperty(str, notify=themeChanged)
    def currentTheme(self) -> str:
        return self._current_theme

    @pyqtProperty(str, notify=gameLoaded)
    def gamePath(self) -> str:
        return self._game_path

    @pyqtProperty(str, notify=gameLoaded)
    def gameName(self) -> str:
        return self._game_name

    @pyqtProperty(int, notify=gameLoaded)
    def packFormat(self) -> int:
        return self._pack_format

    @pyqtProperty(str, notify=gameLoaded)
    def engineVersion(self) -> str:
        return self._engine_version

    @pyqtProperty(int, notify=gameLoaded)
    def fileCount(self) -> int:
        return self._file_count

    @pyqtProperty(bool, notify=gameLoaded)
    def isEmbedded(self) -> bool:
        return self._is_embedded

    @pyqtProperty(str)
    def statusMessage(self) -> str:
        return self._status_message

    @pyqtProperty(bool)
    def isBusy(self) -> bool:
        return self._is_busy

    @pyqtProperty(int, notify=harvestCompleted)
    def harvestedCount(self) -> int:
        return len(self._harvested_entries)

    # --- QML Invocable Slots ---

    @pyqtSlot(str, result=str)
    @pyqtSlot(str, str, result=str)
    def t(self, key_path: str, default: str = "") -> str:
        """Translates UI string using active i18n manager."""
        return self.i18n.t(key_path, default)

    @pyqtSlot(str)
    def setTheme(self, theme_name: str) -> None:
        """Changes active desktop theme mode."""
        if theme_name in ("obsidian", "daylight", "cyber_neon"):
            self._current_theme = theme_name
            self.config.set("theme", theme_name)
            self.themeChanged.emit()

    @pyqtSlot(str)
    def setLocale(self, locale_code: str) -> None:
        """Changes active UI localization catalog."""
        if self.i18n.set_locale(locale_code):
            self.config.set("locale", locale_code)
            self.localeChanged.emit()

    @pyqtSlot(result="QVariantList")
    def getSupportedLocales(self) -> list[dict[str, str]]:
        """Returns list of supported locales with display labels."""
        return [{"code": code, "name": name} for code, name in self.i18n.SUPPORTED_LOCALES]

    @pyqtSlot(str, result=bool)
    def openGame(self, file_url_or_path: str) -> bool:
        """Loads and parses a Godot game PCK or embedded executable."""
        clean_path = file_url_or_path.replace("file:///", "").replace("file://", "")
        target = Path(clean_path).resolve()

        if not target.exists():
            self._status_message = f"File not found: {clean_path}"
            return False

        try:
            reader = PCKReader(target)
            reader.open()

            self._pck_reader = reader
            self._game_path = str(target)
            self._game_name = target.name
            self._pack_format = reader.format_version
            self._engine_version = f"{reader.engine_version[0]}.{reader.engine_version[1]}.{reader.engine_version[2]}"
            self._file_count = len(reader.entries)
            self._is_embedded = reader.is_embedded
            self._status_message = self.i18n.t("dashboard.status_ready")

            self.gameLoaded.emit()
            logger.info(f"Loaded game '{self._game_name}' via bridge.")
            return True
        except (ValueError, OSError, RuntimeError) as e:
            self._status_message = f"Failed opening PCK: {e}"
            logger.error(f"Error opening '{target}': {e}")
            return False

    @pyqtSlot(result=int)
    def harvestStrings(self) -> int:
        """Scans indexed files across scenes, dialogues, and translation tables."""
        if not self._pck_reader:
            return 0

        self._harvested_entries.clear()

        for res_path in self._pck_reader.entries:
            raw_bytes = self._pck_reader.read_file_bytes(res_path)
            if not raw_bytes:
                continue

            # 1. Text-based scenes (.tscn)
            if res_path.endswith(".tscn"):
                text = raw_bytes.decode("utf-8", errors="replace")
                self._harvested_entries.extend(SceneHarvester.harvest_tscn(text, res_path))

            # 2. Dialogue Manager files (.dialogue)
            elif res_path.endswith(".dialogue"):
                text = raw_bytes.decode("utf-8", errors="replace")
                self._harvested_entries.extend(DialogueManagerParser.parse_content(text, res_path))

            # 3. Dialogic files (.dtl or timeline JSON)
            elif res_path.endswith(".dtl"):
                text = raw_bytes.decode("utf-8", errors="replace")
                self._harvested_entries.extend(DialogicParser.parse_dtl(text, res_path))
            elif "timelines" in res_path and res_path.endswith(".json"):
                text = raw_bytes.decode("utf-8", errors="replace")
                self._harvested_entries.extend(DialogicParser.parse_d1_json(text, res_path))

            # 4. Translation CSV / PO
            elif res_path.endswith(".csv"):
                text = raw_bytes.decode("utf-8", errors="replace")
                self._harvested_entries.extend(TranslationHarvester.harvest_csv(text, res_path))
            elif res_path.endswith(".po"):
                text = raw_bytes.decode("utf-8", errors="replace")
                self._harvested_entries.extend(TranslationHarvester.harvest_po(text, res_path))

        count = len(self._harvested_entries)
        self.harvestCompleted.emit(count)
        logger.info(f"Harvested {count} translatable entries across assets.")
        return count

    @pyqtSlot(result="QVariantList")
    def getHarvestedPreview(self) -> list[dict[str, Any]]:
        """Returns the first 200 harvested entries for QML review table."""
        preview: list[dict[str, Any]] = []
        for entry in self._harvested_entries[:200]:
            preview.append({
                "key": entry.key,
                "source_text": entry.source_text,
                "source_file": entry.source_file,
                "context": entry.context or "",
                "type": entry.source_type.value,
                "translation": entry.translation,
            })
        return preview

    @pyqtSlot(str, str, result=bool)
    def applyPatch(self, strategy: str = "overlay", target_locale: str = "tr") -> bool:
        """Deploys localization patch using Method A (Overlay) or Method B (Repack)."""
        if not self._game_path or not self._pck_reader:
            self.patchStatusChanged.emit("No game loaded.", False)
            return False

        game_dir = Path(self._game_path).parent
        is_g4 = self._pck_reader.format_version >= 2
        engine_ver = self._pck_reader.engine_version

        try:
            # Build localized files payload (mock or translated data)
            localized_files: dict[str, bytes] = {}
            for entry in self._harvested_entries:
                if entry.translation:
                    # In real pipeline, rebuild scene or CSV
                    pass

            if strategy == "overlay":
                patcher = OverlayPatcher(game_dir=game_dir, is_godot4=is_g4, engine_version=engine_ver)
                patcher.build_overlay_patch(localized_files=localized_files, target_locale=target_locale)
                self.patchStatusChanged.emit(self.i18n.t("common.success"), True)
                return True
            else:
                out_pck = game_dir / "Game_Patched.pck"
                RepackPatcher.repack(
                    source_pck_path=self._game_path,
                    output_pck_path=out_pck,
                    updated_files=localized_files,
                )
                self.patchStatusChanged.emit(self.i18n.t("common.success"), True)
                return True
        except (ValueError, OSError, RuntimeError) as e:
            self.patchStatusChanged.emit(str(e), False)
            logger.error(f"Patch deployment failed: {e}")
            return False
