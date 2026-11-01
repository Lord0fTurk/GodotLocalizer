"""Main PyQt6 backend bridge connecting QML UI to GodotLocalizer core."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QUrl, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices, QGuiApplication

from src.backend.errors import describe_error
from src.backend.game_files import launch_game
from src.backend.helpers import language_entry, local_path
from src.backend.locale_manager import LocaleManager
from src.backend.log_buffer import LogBuffer
from src.backend.preflight import analyse_package, merge_json_keys
from src.backend.settings_schema import coerce_setting
from src.backend.summaries import completion_summary, font_report, release_payload
from src.core.external.gdre import managed_install_root
from src.core.external.gdre_download import GdreDownloadError, GdreRelease, download_and_install, fetch_latest_release
from src.core.langpack import find_language_root, pack_code, remove_packs
from src.core.languages import engine_supports, languages_for_engine
from src.core.pck.crypto import PCKEncryptionError, PCKKeyRequiredError, PCKWrongKeyError
from src.core.pck.reader import PCKReader
from src.core.pipeline.orchestrator import GodotGameProfile, GodotTranslationPipeline, NothingToTranslateError
from src.core.progress import (
    CancelToken,
    OperationCancelled,
    ProgressEvent,
    ProgressLogFilter,
)
from src.core.translators.base import TranslationEngine
from src.core.translators.manager import TranslationManager
from src.patcher.overlay_patcher import OverlayPatcher
from src.patcher.package import export_package, install_package, patch_status
from src.utils.config import ConfigManager
from src.utils.logger import mask_secrets
from src.utils.paths import get_logs_dir, get_patches_dir, resource_path
from src.version import __version__

logger = logging.getLogger(__name__)

SELECTABLE_ENGINES: tuple[str, ...] = (
    "google", "bing", "deepl", "gemini", "openai", "deepseek", "local_llm", "libretranslate",
)


class AppBackend(QObject):
    """Primary QObject bridge exposed to QML as 'appBackend'."""

    gameChanged = pyqtSignal()
    profileChanged = pyqtSignal()
    progressChanged = pyqtSignal()
    statusChanged = pyqtSignal()
    logsChanged = pyqtSignal()
    statsChanged = pyqtSignal()
    settingsChanged = pyqtSignal()
    toastRequested = pyqtSignal(str, str)
    blockingError = pyqtSignal(str, str)  # (localized title, localized message) for errors that need a dialog
    reviewReady = pyqtSignal(object)
    cacheChanged = pyqtSignal()
    glossaryChanged = pyqtSignal()
    pipelineCompleted = pyqtSignal("QVariantMap", arguments=["summary"])
    gdreReleaseFound = pyqtSignal("QVariantMap", arguments=["release"])
    gdreDownloadProgress = pyqtSignal(float)
    gdreDownloadFinished = pyqtSignal(bool)
    pckKeyNeeded = pyqtSignal(str)
    patchStatusChanged = pyqtSignal()
    jsonKeysSuggested = pyqtSignal("QVariantList", arguments=["candidates"])
    legacyScriptsFound = pyqtSignal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state_lock = threading.RLock()
        self._worker_thread: threading.Thread | None = None
        self.config = ConfigManager()
        self.tm = TranslationManager(config=self.config)
        self.pipeline = GodotTranslationPipeline(translation_manager=self.tm)
        self.pipeline.progress_callback = self._handle_progress
        self._cancel_token: CancelToken | None = None

        self._game_path: str = ""
        self._profile: GodotGameProfile | None = None
        self._status_key: str = "dashboard.status_ready"
        self._status_params: dict[str, Any] = {}
        self._is_busy: bool = False
        self._progress_val: float = 0.0
        self._progress_msg: str = ""
        self._log_buffer = LogBuffer()
        self._progress_log_filter = ProgressLogFilter()
        self._preflight_running: bool = False
        self._preflight_done: set[tuple[str, str]] = set()
        self._pending_game_path: str = ""

    @pyqtProperty(str, constant=True)
    def version(self) -> str:
        return __version__

    def _translate_text(self, key_or_text: str, **kwargs: Any) -> str:
        """Resolve localized string if key exists in LocaleManager."""
        lm = LocaleManager.get_instance()
        if lm:
            tmpl = lm.t(key_or_text, key_or_text)
            for k, v in kwargs.items():
                tmpl = tmpl.replace(f"{{{k}}}", str(v))
            return mask_secrets(tmpl)
        return mask_secrets(key_or_text)

    def _set_status(self, key_or_text: str, **kwargs: Any) -> None:
        with self._state_lock:
            self._status_key = key_or_text
            self._status_params = kwargs
        self.statusChanged.emit()

    def _notify(self, level: str, key: str, **params: Any) -> None:
        self.add_log(level, key, **params)
        self._set_status(key, **params)
        self.toastRequested.emit(self._translate_text(key, **params), level)

    def _notify_exception(self, exc: BaseException) -> None:
        """Localized error toast; errors the user must act on also open a dialog (no duplicate toast)."""
        info = describe_error(exc)
        if not info.blocking:
            self._notify("error", info.key, **info.params)
            return
        self.add_log("error", info.key, **info.params)  # the full text stays in the console log
        self._set_status("errors.status_failed")  # the dashboard status line only has room for a few words
        self.blockingError.emit(
            self._translate_text("errors.dialog_title"), self._translate_text(info.key, **info.params)
        )

    def add_log(self, level: str, message: str, **kwargs: Any) -> None:
        self._log_buffer.add(level, self._translate_text(message, **kwargs))
        self.logsChanged.emit()

    def _handle_progress(self, event: ProgressEvent) -> None:
        message = self._translate_text(event.key, **event.message_params())
        with self._state_lock:
            self._progress_val = event.fraction
            self._progress_msg = message
            should_log = self._progress_log_filter.should_log(event)
        self.progressChanged.emit()
        if should_log:
            self.add_log("info", message)

    # --- Properties exposed to QML ---

    @pyqtProperty(str, notify=gameChanged)
    def gamePath(self) -> str:
        return self._game_path

    @pyqtProperty(str, notify=gameChanged)
    def gameName(self) -> str:
        return self._profile.game_name if self._profile else ""

    @pyqtProperty(str, notify=profileChanged)
    def godotVersion(self) -> str:
        return self._profile.engine_version if self._profile else "Unknown"

    @pyqtProperty(str, notify=profileChanged)
    def packFormat(self) -> str:
        if not self._profile:
            return "—"
        return f"Format {self._profile.pack_format} (Godot {self._profile.major_version}.x)"

    @pyqtProperty(int, notify=profileChanged)
    def fileCount(self) -> int:
        return self._profile.file_count if self._profile else 0

    @pyqtProperty(bool, notify=profileChanged)
    def isEmbedded(self) -> bool:
        return bool(self._profile and self._profile.is_embedded)

    @pyqtProperty(bool, notify=statusChanged)
    def isBusy(self) -> bool:
        return self._is_busy

    @pyqtProperty(str, notify=statusChanged)
    def statusText(self) -> str:
        with self._state_lock:
            key, params = self._status_key, dict(self._status_params)
        return self._translate_text(key, **params)

    @pyqtProperty(float, notify=progressChanged)
    def progress(self) -> float:
        return self._progress_val

    @pyqtProperty(str, notify=progressChanged)
    def progressText(self) -> str:
        return self._progress_msg

    @pyqtProperty("QVariantList", notify=logsChanged)
    def logs(self) -> list[dict[str, str]]:
        return self._log_buffer.snapshot()

    @pyqtProperty("QVariantMap", notify=settingsChanged)
    def settings(self) -> dict[str, Any]:
        return self.config.to_dict()

    @pyqtProperty(str, notify=settingsChanged)
    def targetLanguage(self) -> str:
        return str(self.config.get("target_language", "tr"))

    @pyqtProperty(str, notify=settingsChanged)
    def sourceLanguage(self) -> str:
        return str(self.config.get("source_language", "auto"))

    @pyqtProperty(str, notify=settingsChanged)
    def engine(self) -> str:
        return str(self.config.get("engine", "google"))

    @pyqtProperty("QVariantList", constant=True)
    def selectableEngines(self) -> list[str]:
        return list(SELECTABLE_ENGINES)

    @pyqtProperty("QVariantList", notify=settingsChanged)
    def supportedEngines(self) -> list[dict[str, str]]:
        return [
            {"id": e, "name": self._translate_text(f"engines.{e}")}
            for e in SELECTABLE_ENGINES
        ]

    @pyqtProperty(str, notify=profileChanged)
    def recommendedRoute(self) -> str:
        if not self._profile:
            return "—"
        return "Route A: PCK Overlay (override.cfg)"

    @pyqtProperty(str, notify=settingsChanged)
    def selectedEngine(self) -> str:
        return self.engine

    @pyqtProperty(str, notify=progressChanged)
    def progressMessage(self) -> str:
        return self._progress_msg

    @pyqtProperty(bool, notify=settingsChanged)
    def targetIsRtl(self) -> bool:
        return self.targetLanguage in ("ar", "fa", "he", "ur")

    @pyqtProperty(bool, notify=settingsChanged)
    def rtlStaticShaping(self) -> bool:
        return bool(self.config.get("rtl_shaping", True))

    @pyqtProperty(bool, notify=settingsChanged)
    def languagesSupportedByEngine(self) -> bool:
        try:
            e = TranslationEngine(self.engine)
            return engine_supports(e, self.targetLanguage)
        except Exception:
            return True

    @pyqtProperty("QVariantList", notify=settingsChanged)
    def availableLanguages(self) -> list[dict[str, str]]:
        eng = self.config.get("engine", "google")
        try:
            e = TranslationEngine(eng)
            langs = languages_for_engine(e)
        except ValueError:
            langs = []
        return [language_entry(lang) for lang in langs]

    @pyqtProperty("QVariantList", notify=settingsChanged)
    def supportedTargetLanguages(self) -> list[dict[str, str]]:
        return self.availableLanguages

    @pyqtProperty(int, notify=cacheChanged)
    def cacheEntryCount(self) -> int:
        return self.pipeline.cache.count()

    @pyqtProperty("QVariantList", notify=glossaryChanged)
    def glossaryTerms(self) -> list[dict[str, str]]:
        terms = self.pipeline.glossary.terms
        return [{"term": k, "replacement": v} for k, v in terms.items()]

    @pyqtProperty("QVariantMap", constant=True)
    def windowGeometry(self) -> dict[str, Any]:
        return {
            "width": int(self.config.get("window_width", 0) or 0),
            "height": int(self.config.get("window_height", 0) or 0),
            "maximized": bool(self.config.get("window_maximized", False)),
        }

    # --- QML Invocable Slots ---

    @pyqtSlot(str)
    def selectGame(self, path_or_url: str) -> None:
        """Inspects target Godot package or executable."""
        path = local_path(path_or_url)
        self._sync_pck_key()
        try:
            profile = self.pipeline.profile_game(path)
            self._profile = profile
            self._game_path = profile.game_path
            self.config.set("last_game_path", profile.game_path)
            self.gameChanged.emit()
            self.profileChanged.emit()
            self.patchStatusChanged.emit()
            self._notify("info", "dashboard.game_selected", path=profile.game_name)
        except PCKEncryptionError as e:
            self._pending_game_path = path
            reason = "missing" if isinstance(e, PCKKeyRequiredError) else "wrong" if isinstance(e, PCKWrongKeyError) else "invalid"
            self.pckKeyNeeded.emit(reason)
        except Exception as e:
            self._notify_exception(e)

    @pyqtSlot(str)
    def submitPckKey(self, key: str) -> None:
        """Stores the encryption key the user typed into the prompt and retries the pending game."""
        self.config.set("pck_encryption_key", key.strip())
        self.settingsChanged.emit()
        pending, self._pending_game_path = self._pending_game_path, ""
        if pending:
            self.selectGame(pending)

    def _sync_pck_key(self) -> None:
        """Hands per-run user settings (PCK key, fallback font, RTL shaping) to the pipeline."""
        self.pipeline.pck_key = str(self.config.get("pck_encryption_key", "") or "").strip()
        self.pipeline.gdre_path = str(self.config.get("gdre_path", "") or "").strip()
        self.pipeline.json_text_keys = str(self.config.get("json_text_keys", "") or "")
        self.pipeline.bilingual = bool(self.config.get("bilingual_mode", False))
        self.pipeline.bilingual_template = str(self.config.get("bilingual_template", "") or "{target}\n({source})")
        use_font = self.config.get("font_preset", "") in ("custom", "custom_ttf")
        self.pipeline.font_path = str(self.config.get("custom_font_path", "") or "").strip() if use_font else ""
        self.pipeline.rtl_shaping = bool(self.config.get("rtl_shaping", True))

    @pyqtSlot(str, "QVariant")
    def setSetting(self, key: str, value: Any) -> None:
        """Coerces, saves, and updates settings reactively."""
        try:
            valid_val = coerce_setting(key, value)
            self.config.set(key, valid_val)
            self.settingsChanged.emit()
        except Exception as e:
            logger.warning(f"Error setting '{key}': {e}")

    @pyqtSlot(str)
    def setTargetLanguage(self, code: str) -> None:
        self.config.set("target_language", code)
        self.settingsChanged.emit()

    @pyqtSlot(str)
    def setSourceLanguage(self, code: str) -> None:
        self.config.set("source_language", code)
        self.settingsChanged.emit()

    @pyqtSlot(str)
    def setEngine(self, engine_name: str) -> None:
        self.config.set("engine", engine_name)
        self.settingsChanged.emit()

    @pyqtSlot(str)
    def setSelectedEngine(self, engine_name: str) -> None:
        self.setEngine(engine_name)

    @pyqtSlot()
    def startLocalization(self) -> None:
        self.startTranslation()

    @pyqtSlot()
    def stopTranslation(self) -> None:
        self.cancelTranslation()

    @pyqtSlot(bool)
    def setRtlStaticShaping(self, enabled: bool) -> None:
        self.config.set("rtl_shaping", enabled)
        self.settingsChanged.emit()

    @pyqtSlot(str, str)
    def addGlossaryTerm(self, term: str, replacement: str) -> None:
        if term.strip() and replacement.strip():
            self.pipeline.glossary.add_term(term, replacement)
            self.glossaryChanged.emit()
            self._notify("success", "settings.glossary_added", term=term)

    @pyqtSlot(str)
    def removeGlossaryTerm(self, term: str) -> None:
        if self.pipeline.glossary.remove_term(term):
            self.glossaryChanged.emit()
            self._notify("info", "settings.glossary_removed", term=term)

    @pyqtSlot(str)
    def addTranslationMemoryFile(self, path_or_url: str) -> None:
        """Appends a translation-memory file to the ``tm_files`` setting (one path per line)."""
        path = Path(local_path(path_or_url))
        if not path.is_file():
            return
        entries = [line for line in str(self.config.get("tm_files", "") or "").splitlines() if line.strip()]
        if str(path) not in entries:
            entries.append(str(path))
            self.setSetting("tm_files", "\n".join(entries))
        self._notify("info", "settings.tm_imported", path=path.name)

    @pyqtProperty("QVariantMap", notify=patchStatusChanged)
    def patchStatus(self) -> dict[str, Any]:
        """Is the selected game's folder patched, in which language, and was the game updated since?"""
        if not self._game_path:
            return {"patched": False, "locale": "", "gameUpdated": False}
        status = patch_status(Path(self._game_path).parent, self._game_path)
        return {"patched": status.patched, "locale": status.locale, "gameUpdated": status.game_updated}

    @pyqtSlot()
    def exportPatchPackage(self) -> None:
        """Zips the deployed patch (not the game) so it can be shared; the recipient needs nothing else."""
        if not self._game_path:
            return
        game = Path(self._game_path)
        status = patch_status(game.parent, game)
        if not status.patched:
            self._notify("warning", "dashboard.export_needs_patch")
            return
        try:
            target = get_patches_dir() / f"{game.stem}-{status.locale or 'patch'}-patch.zip"
            out = export_package(game.parent, target, game.stem)
        except (OSError, ValueError) as exc:
            self._notify_exception(exc)
            return
        self._notify("success", "dashboard.patch_exported", path=str(out))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(out.parent)))

    @pyqtSlot(str)
    def installPatchPackage(self, zip_path_or_url: str) -> None:
        """Installs a patch zip made by this app into the selected game's folder (override.cfg is merged)."""
        if not self._game_path:
            self._notify("warning", "dashboard.install_needs_game")
            return
        try:
            result = install_package(local_path(zip_path_or_url), Path(self._game_path).parent, self._game_path)
        except (OSError, ValueError) as exc:
            self._notify_exception(exc)
            return
        for warning in result.warnings:
            self.add_log("warning", warning)
        self._notify("warning" if result.warnings else "success", "dashboard.patch_installed", locale=result.locale)
        self.patchStatusChanged.emit()

    @pyqtSlot()
    def openPatchFolder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_patches_dir())))

    @pyqtSlot()
    def startTranslation(self) -> None:
        """Analyses the package first (asking the user only if something is missing), then translates."""
        if self._is_busy or self._preflight_running or not self._game_path:
            return
        if ("ready", self._game_path) in self._preflight_done:
            self._begin_translation()
            return
        self._preflight_running = True
        self._set_status("dashboard.status_profiling")
        threading.Thread(target=self._run_preflight, daemon=True).start()

    def _preflight_findings(self) -> tuple[list[dict[str, Any]], int]:
        """JSON fields that look like text and compiled scripts only GDRE Tools can read."""
        self._sync_pck_key()
        return analyse_package(self._game_path, self.pipeline.pck_key, self.pipeline.json_text_keys, self.pipeline.gdre_path)

    def _run_preflight(self) -> None:
        game = self._game_path
        try:
            candidates, legacy = self._preflight_findings()
        except Exception as exc:
            logger.warning(f"Pre-flight analysis failed: {exc}")
            candidates, legacy = [], 0
        self._preflight_running = False
        if candidates and ("json", game) not in self._preflight_done:
            self._preflight_done.add(("json", game))
            if any(c["ask"] for c in candidates):
                self.jsonKeysSuggested.emit(candidates)  # only when something is genuinely unclear
                return
            chosen = [str(c["key"]) for c in candidates if c["selected"]]
            if chosen:
                self._merge_json_keys(chosen)
                self.add_log("info", "dashboard.json_auto_applied", keys=", ".join(chosen))
        if legacy and ("gdre", game) not in self._preflight_done:
            self._preflight_done.add(("gdre", game))
            self.legacyScriptsFound.emit(legacy)
            return
        self._preflight_done.add(("ready", game))
        self._begin_translation()

    @pyqtSlot("QVariantList")
    def applyJsonKeys(self, keys: list[str]) -> None:
        """Adds the JSON fields the user approved to the harvest settings and continues."""
        self._merge_json_keys([str(k) for k in keys])
        self.startTranslation()

    def _merge_json_keys(self, keys: list[str]) -> None:
        self.config.set("json_text_keys", merge_json_keys(str(self.config.get("json_text_keys", "") or ""), keys))
        self.settingsChanged.emit()

    @pyqtSlot()
    def skipJsonKeys(self) -> None:
        self.startTranslation()

    @pyqtSlot(bool)
    def answerLegacyScripts(self, download: bool) -> None:
        """Download GDRE Tools (after the usual confirmation) or continue without those scripts."""
        if download:
            self.checkGdreRelease()
        else:
            self.startTranslation()

    def _begin_translation(self) -> None:
        """Starts the autonomous translation pipeline in a background thread."""
        if self._is_busy or not self._game_path:
            return

        self._is_busy = True
        self._progress_val = 0.0
        self.statusChanged.emit()
        self.progressChanged.emit()

        target_lang = self.targetLanguage
        source_lang = self.sourceLanguage
        engine_str = self.engine
        strategy = self.config.get("patch_strategy", "overlay")
        try:
            engine_enum = TranslationEngine(engine_str)
        except ValueError:
            engine_enum = TranslationEngine.GOOGLE

        self._cancel_token = CancelToken()
        self._sync_pck_key()

        self._worker_thread = threading.Thread(
            target=self._translation_worker, args=(target_lang, source_lang, engine_enum, strategy), daemon=True,
        )
        self._worker_thread.start()

    def _translation_worker(self, target_lang: str, source_lang: str, engine: TranslationEngine, strategy: str) -> None:
        """Runs the pipeline off the UI thread and reports the outcome through signals."""
        try:
            session = self.pipeline.run(
                game_path=self._game_path,
                target_lang=target_lang,
                source_lang=source_lang,
                engine=engine,
                cancel_token=self._cancel_token,
                strategy=strategy,
            )
            self.reviewReady.emit(session)
            self._notify("success", "dashboard.status_completed", count=len(session.rows))
            if find_language_root(Path(self._game_path).parent) is not None:
                self._notify("info", "dashboard.langpack_hint", code=pack_code(target_lang))
            self.patchStatusChanged.emit()
            self.pipelineCompleted.emit(completion_summary(session, self._game_path, self.gameName, target_lang))
        except OperationCancelled:
            self._notify("warning", "dashboard.status_cancelled")
        except NothingToTranslateError:
            self._notify("warning", "dashboard.nothing_found")
        except Exception as e:
            logger.exception(f"Pipeline error: {e}")
            self._notify_exception(e)
        finally:
            self._is_busy = False
            self.statusChanged.emit()

    @pyqtSlot()
    def cancelTranslation(self) -> None:
        if self._cancel_token:
            self._cancel_token.cancel()
            self._set_status("dashboard.status_cancelling")

    @pyqtSlot()
    def clearLogs(self) -> None:
        self._log_buffer.clear()
        self.logsChanged.emit()

    @pyqtSlot()
    def copyLogs(self) -> None:
        QGuiApplication.clipboard().setText(self._log_buffer.as_text())
        self.toastRequested.emit(self._translate_text("console.logs_copied"), "success")

    @pyqtSlot()
    def openLogsFolder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_logs_dir())))

    @pyqtSlot()
    def openGameFolder(self) -> None:
        if self._game_path:
            p = Path(self._game_path).parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    @pyqtSlot(int, int, bool)
    def saveWindowState(self, width: int, height: int, maximized: bool) -> None:
        self.config.set("window_width", width)
        self.config.set("window_height", height)
        self.config.set("window_maximized", maximized)

    @pyqtSlot(str, result=str)
    def get_asset_url(self, asset_name: str) -> str:
        for candidate in (
            resource_path(asset_name),
            resource_path(Path("src/gui/qml/fonts") / asset_name),
        ):
            if candidate.exists():
                return QUrl.fromLocalFile(str(candidate)).toString()
        return ""

    @pyqtSlot(str)
    def onUiLanguageChanged(self, lang_code: str) -> None:
        """Persist active UI language and refresh localized properties."""
        if lang_code and self.config.get("ui_language") != lang_code:
            self.config.set("ui_language", lang_code)
        self.statusChanged.emit()
        self.settingsChanged.emit()

    @pyqtSlot()
    def restoreLastSession(self) -> None:
        """Restores the previously selected game path on startup if valid."""
        last_path = str(self.config.get("last_game_path", "") or "").strip()
        if last_path and Path(last_path).exists():
            self.selectGame(last_path)

    @pyqtSlot()
    def launchGame(self) -> None:
        """Launches the target Godot game executable."""
        if not self._game_path:
            return
        try:
            launch_game(self._game_path)
        except OSError as exc:
            self._notify_exception(exc)

    def shutdown(self) -> None:
        """Cleanly cancels running tasks and persists configuration on exit."""
        if self._cancel_token:
            self._cancel_token.cancel()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
        self.config.save()

    @pyqtSlot(str)
    def extractPck(self, output_dir_or_url: str) -> None:
        """Extracts all indexed files from current PCK or embedded executable to disk."""
        if not self._game_path:
            self._notify("error", "toolbox.extract_no_game")
            return
        out_dir = local_path(output_dir_or_url)
        if not out_dir:
            return
        self._sync_pck_key()

        def _extract_worker() -> None:
            try:
                self._notify("info", "toolbox.extracting_start")
                with PCKReader(self._game_path, key=self.pipeline.pck_key) as reader:
                    count = reader.extract_all(out_dir)
                self._notify("success", "toolbox.extract_complete", count=count, path=out_dir)
            except Exception as e:
                logger.exception(f"Failed to extract PCK: {e}")
                self._notify_exception(e)

        threading.Thread(target=_extract_worker, daemon=True).start()

    @pyqtSlot(str, str, result="QVariantMap")
    def diagnoseTofu(self, font_path_or_url: str, target_lang: str) -> dict[str, Any]:
        """Scans font file CMAP table for target language glyph coverage."""
        return font_report(local_path(font_path_or_url), target_lang or self.targetLanguage)

    @pyqtSlot()
    def checkGdreRelease(self) -> None:
        """Looks up the newest GDRE Tools release (metadata only) and asks the UI for confirmation."""

        def _lookup() -> None:
            try:
                release = fetch_latest_release()
            except GdreDownloadError as exc:
                self._notify_exception(exc)
                return
            self._gdre_release = release
            self.gdreReleaseFound.emit(release_payload(release))

        threading.Thread(target=_lookup, daemon=True).start()

    @pyqtSlot()
    def downloadGdre(self) -> None:
        """Downloads the release the user just confirmed, verifies it and stores its path in the settings."""
        release: GdreRelease | None = getattr(self, "_gdre_release", None)
        if release is None:
            return

        def _install() -> None:
            try:
                exe = download_and_install(release, managed_install_root(), self.gdreDownloadProgress.emit)
            except GdreDownloadError as exc:
                self._notify_exception(exc)
                self.gdreDownloadFinished.emit(False)
                return
            self.config.set("gdre_path", str(exe))
            self.settingsChanged.emit()
            self._notify("success", "settings.gdre_installed", path=str(exe))
            self.gdreDownloadFinished.emit(True)

        threading.Thread(target=_install, daemon=True).start()

    @pyqtSlot()
    def revertVanilla(self) -> None:
        """Removes injected PCK overlay and override.cfg restoring original state."""
        if not self._game_path:
            return
        game_dir = Path(self._game_path).parent
        removed = 0
        try:
            removed = OverlayPatcher(game_dir=game_dir).remove_patch() + remove_packs(game_dir)
        except OSError as e:
            logger.warning(f"Could not remove patch files: {e}")
        self._notify("info", "toolbox.revert_success", count=removed)
        self.patchStatusChanged.emit()

    @pyqtSlot()
    def clearCache(self) -> None:
        """Purges cached translation strings."""
        try:
            count = self.pipeline.cache.clear()
            self._notify("success", "toolbox.cache_cleared", count=count)
            self.cacheChanged.emit()
        except Exception as e:
            self._notify_exception(e)
