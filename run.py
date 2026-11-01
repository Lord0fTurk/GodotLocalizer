"""Bootstrap entry point for GodotLocalizer desktop application."""

import os
import sys
from pathlib import Path

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtQml import QQmlApplicationEngine

from src.backend.app_backend import AppBackend
from src.backend.editor_backend import EditorBackend
from src.backend.locale_manager import LocaleManager
from src.utils.logger import logger
from src.utils.paths import resource_path
from src.version import APP_NAME, __version__


def _attach_parent_console() -> None:
    """Windowed Windows builds have no console; attach to the caller's so CLI output is visible."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            sys.stdout = sys.stderr = open("CONOUT$", "w", encoding="utf-8")
    except Exception:  # best effort: output is only cosmetic, the exit code still works
        pass


def self_check() -> int:
    """Verifies a frozen build contains everything it needs (``run.py --self-check``)."""
    import json

    problems: list[str] = []
    for module in ("PyQt6.QtQml", "PyQt6.QtQuick", "cryptography.hazmat.primitives.ciphers", "fontTools.ttLib",
                   "src.cli", "src.core.pipeline.orchestrator", "src.backend.app_backend", "curl_cffi"):
        try:
            __import__(module)
        except Exception as exc:
            problems.append(f"import {module}: {exc}")
    for asset in ("src/gui/qml/Main.qml", "src/gui/i18n/en.json", "src/gui/i18n/tr.json", "icon.png"):
        if not resource_path(asset).is_file():
            problems.append(f"missing asset {asset}")
    try:
        json.loads(resource_path("src/gui/i18n/en.json").read_text(encoding="utf-8"))
    except Exception as exc:
        problems.append(f"en.json: {exc}")
    for problem in problems:
        print(f"FAIL: {problem}")
    print(f"{APP_NAME} {__version__}: " + ("self-check failed" if problems else "self-check OK"))
    return 1 if problems else 0


def main() -> int:
    """Runs the command-line interface for ``run.py translate ...`` style calls, otherwise the GUI."""
    from src.cli import COMMANDS
    from src.cli import main as cli_main

    if "--self-check" in sys.argv[1:]:
        return self_check()
    if len(sys.argv) > 1 and sys.argv[1] in (*COMMANDS, "--version", "-h", "--help"):
        _attach_parent_console()
        return cli_main(sys.argv[1:])
    return run_gui()


def run_gui() -> int:
    """Initializes and runs the PyQt6 / QML GUI application."""
    logger.info(f"Starting {APP_NAME} v{__version__}...")

    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Material")
    os.environ.setdefault("QT_QUICK_CONTROLS_MATERIAL_THEME", "Dark")

    app = QGuiApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)

    for icon_name in ("icon.ico", "icon.png"):
        icon_file = resource_path(icon_name)
        if icon_file.is_file():
            app.setWindowIcon(QIcon(str(icon_file)))
            break

    # Instantiate core backend components
    app_backend = AppBackend()
    default_ui_lang = str(app_backend.config.get("ui_language", "tr") or "tr")
    locale_manager = LocaleManager(default_lang=default_ui_lang)
    editor_backend = EditorBackend(
        translation_manager=app_backend.tm,
        pipeline=app_backend.pipeline,
    )

    # Link signals between locale manager, pipeline, and review editor
    locale_manager.languageChanged.connect(app_backend.onUiLanguageChanged)
    locale_manager.languageChanged.connect(editor_backend.onUiLanguageChanged)
    app_backend.reviewReady.connect(editor_backend.loadSession)

    engine = QQmlApplicationEngine()

    # Expose backends to QML environment
    engine.rootContext().setContextProperty("appBackend", app_backend)
    engine.rootContext().setContextProperty("localeManager", locale_manager)
    engine.rootContext().setContextProperty("editorBackend", editor_backend)
    engine.rootContext().setContextProperty("bridge", app_backend)

    qml_dir = Path(__file__).resolve().parent / "src" / "gui" / "qml"
    engine.addImportPath(str(qml_dir))
    engine.addImportPath(str(qml_dir / "components"))

    main_qml_path = qml_dir / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(main_qml_path)))

    if not engine.rootObjects():
        logger.error("Failed to load QML interface. Exiting...")
        return -1

    QTimer.singleShot(0, app_backend.restoreLastSession)

    if os.environ.get("GODOTLOCALIZER_SMOKE_TEST") == "1":
        QTimer.singleShot(800, app.quit)

    logger.info("GUI window initialized successfully.")
    exit_code = app.exec()
    app_backend.shutdown()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
