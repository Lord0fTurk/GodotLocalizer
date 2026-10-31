"""Runs inside its own process (see test_qml_smoke.py): a GUI application cannot share a process with
the QCoreApplication that other tests create."""

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Material")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QCoreApplication, QObject, QUrl, qInstallMessageHandler
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine

from src.backend.app_backend import AppBackend
from src.backend.editor_backend import EditorBackend
from src.backend.locale_manager import LocaleManager
from src.utils.config import ConfigManager

QML_DIR = Path(__file__).resolve().parents[1] / "src" / "gui" / "qml"
HOST = b"""
import QtQuick
import QtQuick.Controls
import "views"
import "components"

ApplicationWindow {
    width: 1000; height: 800; visible: true
    SettingsView { anchors.fill: parent }
    DashboardView { anchors.fill: parent; visible: false }
    EditorView { anchors.fill: parent; visible: false }
    PckKeyDialog { objectName: "pckKeyDialog" }
    JsonKeysDialog { objectName: "jsonKeysDialog" }
    LegacyScriptsDialog { objectName: "legacyScriptsDialog" }
}
"""




def main() -> int:
    app = QGuiApplication([])
    messages: list[str] = []
    qInstallMessageHandler(lambda _mode, _ctx, message: messages.append(message))
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch("src.backend.app_backend.ConfigManager", lambda: ConfigManager(Path(tmp) / "config.json")):
            backend = AppBackend()
        locale = LocaleManager(default_lang="en")
        editor = EditorBackend(translation_manager=backend.tm, pipeline=backend.pipeline)
        engine = QQmlApplicationEngine()
        for name, obj in (("appBackend", backend), ("localeManager", locale), ("editorBackend", editor), ("bridge", backend)):
            engine.rootContext().setContextProperty(name, obj)
        engine.addImportPath(str(QML_DIR))
        engine.addImportPath(str(QML_DIR / "components"))
        engine.loadData(HOST, QUrl.fromLocalFile(str(QML_DIR / "Host.qml")))
        if not engine.rootObjects():
            print("FAIL: SettingsView failed to load:", "; ".join(messages[:5]))
            return 1

        dialog = engine.rootObjects()[0].findChild(QObject, "gdreDialog")
        if dialog is None or dialog.property("opened"):
            print("FAIL: dialog missing or open at start")
            return 1
        backend.gdreReleaseFound.emit(
            {"tag": "v9", "file": "GDRE_tools-v9-windows.zip", "sizeMb": 40.6, "source": "https://github.com/x",
             "sha256": "ab" * 32, "license": "MIT"}
        )
        deadline = time.monotonic() + 3
        while not dialog.property("opened") and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        if not dialog.property("opened"):
            print("FAIL: consent dialog did not open", messages[:5])
            return 1
        if dialog.property("release")["file"] != "GDRE_tools-v9-windows.zip":
            print("FAIL: release details were not passed to the dialog")
            return 1
        root = engine.rootObjects()[0]
        for name, emit in (
            ("pckKeyDialog", lambda: backend.pckKeyNeeded.emit("wrong")),
            ("jsonKeysDialog", lambda: backend.jsonKeysSuggested.emit(
                [{"key": "card_text", "count": 30, "sample": "Deal 6 damage", "reason": "maybe", "selected": False, "ask": True}])),
            ("legacyScriptsDialog", lambda: backend.legacyScriptsFound.emit(7)),
        ):
            popup = root.findChild(QObject, name)
            if popup is None:
                print("FAIL: missing", name)
                return 1
            emit()
            deadline = time.monotonic() + 3
            while not popup.property("opened") and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                time.sleep(0.02)
            if not popup.property("opened"):
                print("FAIL:", name, "did not open")
                return 1
        summary = {
            "total": 1355, "translated": 1273, "target": "tr", "total_harvested": 1355, "translated_count": 1273,
            "failed_count": 30, "rejected_count": 40, "unchanged_count": 12, "success_rate": "93.9%",
            "game_name": "game.pck", "game_path": "D:/Games/Some Long Folder Name/Another Folder/game.pck",
            "target_lang": "tr", "has_exe": True,
        }
        backend.pipelineCompleted.emit(summary)
        done = root.findChild(QObject, "completionDialog")
        deadline = time.monotonic() + 3
        while done is not None and not done.property("opened") and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        if done is None or not done.property("opened"):
            print("FAIL: completion dialog did not open")
            return 1
        for _ in range(10):
            QCoreApplication.processEvents()
        if done.property("contentFits") is not True:
            print("FAIL: the completion dialog's content sticks out of its card")
            return 1
        markers = ("ReferenceError", "TypeError", "is not a type", "Unable to assign", "Cannot assign",
                   "Cannot read property", "Binding loop")
        problems = [m for m in messages if any(k in m for k in markers)]
        if problems:
            print("FAIL: QML reported problems:", problems[:5])
            return 1
        del engine
    _ = app
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
