"""End-to-end checks against the real Godot engine (opt-in).

Set ``GODOT4_BIN`` and/or ``GODOT3_BIN`` to the official editor executable
(for example ``Godot_v4.7.2-stable_win64.exe``; on Windows the ``*_console.exe`` sibling is used for output).
Each test builds a tiny project, exports it as a PCK, runs the real pipeline phases to create the patch and
runs the game again, so it catches mistakes that only the engine's own loader notices (for example padded
path lengths in the PCK directory, which Godot reads literally).
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

from src.core.pck.reader import PCKReader
from src.core.pipeline.orchestrator import GodotTranslationPipeline
from src.patcher.overlay_patcher import OverlayPatcher

FAKE = {
    "Hello World": "Hallo Welt",
    "Start Game": "Spiel starten",
    "Exit Game": "Spiel beenden",
    "Start the Adventure": "Das Abenteuer beginnen",
    "Score: %d": "Punkte: %d",
}
CSV = "keys,en\nMENU_EXIT,Exit Game\nMENU_START,Start the Adventure\nMENU_SCORE,Score: %d\n"
PRESET = """[preset.0]

name="Pack"
platform="Windows Desktop"
runnable=true
custom_features=""
export_filter="all_resources"
include_filter=""
exclude_filter=""
export_path=""
script_export_mode=1
script_encryption_key=""

[preset.0.options]
"""

G4_FILES = {
    "project.godot": """config_version=5

[application]

config/name="LocTest"
run/main_scene="res://main.tscn"

[autoload]

GameState="*res://game_state.gd"

[internationalization]

locale/translations=PackedStringArray("res://strings.en.translation")
""",
    "game_state.gd": "extends Node\n",
    "main.tscn": """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://main.gd" id="1"]

[node name="Main" type="Control"]
script = ExtResource("1")

[node name="Label" type="Label" parent="."]
text = "Hello World"

[node name="Button" type="Button" parent="."]
text = "Start Game"
""",
    "main.gd": """extends Control

func _ready() -> void:
    var names := []
    for c in get_tree().root.get_children():
        names.append(str(c.name))
    var lines := [
        "locale=" + TranslationServer.get_locale(),
        "label=" + tr($Label.text),
        "button=" + tr($Button.text),
        "key_exit=" + tr("MENU_EXIT"),
        "key_start=" + tr("MENU_START"),
        "key_score=" + (tr("MENU_SCORE") % 42),
        "root_children=" + ",".join(names),
    ]
    var f := FileAccess.open(OS.get_environment("LOC_REPORT"), FileAccess.WRITE)
    f.store_string("\\n".join(lines))
    f.close()
    get_tree().quit()
""",
}

G3_FILES = {
    "project.godot": """config_version=4

[application]

config/name="LocTest3"
run/main_scene="res://main.tscn"

[autoload]

GameState="*res://game_state.gd"

[locale]

translations=PoolStringArray( "res://strings.en.translation" )
""",
    "game_state.gd": "extends Node\n",
    "main.tscn": """[gd_scene load_steps=2 format=2]

[ext_resource path="res://main.gd" type="Script" id=1]

[node name="Main" type="Control"]
script = ExtResource( 1 )

[node name="Label" type="Label" parent="."]
text = "Hello World"

[node name="Button" type="Button" parent="."]
text = "Start Game"
""",
    "main.gd": """extends Control

func _ready():
    var names = []
    for c in get_tree().root.get_children():
        names.append(str(c.name))
    var lines = [
        "locale=" + TranslationServer.get_locale(),
        "label=" + tr($Label.text),
        "button=" + tr($Button.text),
        "key_exit=" + tr("MENU_EXIT"),
        "key_start=" + tr("MENU_START"),
        "key_score=" + (tr("MENU_SCORE") % 42),
        "root_children=" + PoolStringArray(names).join(","),
    ]
    var f = File.new()
    f.open(OS.get_environment("LOC_REPORT"), File.WRITE)
    f.store_string(PoolStringArray(lines).join("\\n"))
    f.close()
    get_tree().quit()
""",
}


def _run(args: list[str | Path], cwd: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        [str(a) for a in args], cwd=cwd, env={**os.environ, **(env or {})},
        capture_output=True, text=True, timeout=240, check=False,
    )
    return result.stdout + result.stderr


class _EngineTests:
    """Shared engine checks; concrete classes bind a Godot version."""

    env_var = ""
    files: ClassVar[dict[str, str]] = {}

    @classmethod
    def setUpClass(cls) -> None:
        exe = os.environ.get(cls.env_var, "")
        if not exe or not Path(exe).is_file():
            raise unittest.SkipTest(f"{cls.env_var} is not set")
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.editor = Path(exe)
        console = cls.editor.with_name(cls.editor.stem + "_console.exe")
        cls.editor_cli = console if console.is_file() else cls.editor
        cls.project, cls.game = base / "proj", base / "game"
        cls.project.mkdir()
        cls.game.mkdir()
        for name, text in cls.files.items():
            (cls.project / name).write_text(text, encoding="utf-8")
        (cls.project / "strings.csv").write_text(CSV, encoding="utf-8")
        (cls.project / "export_presets.cfg").write_text(PRESET, encoding="utf-8")
        _run([cls.editor_cli, "--headless", "--path", cls.project, "--editor", "--quit"], base)
        _run([cls.editor_cli, "--headless", "--path", cls.project, "--export-pack", "Pack", cls.game / "game.pck"], base)
        if not (cls.game / "game.pck").is_file():
            raise unittest.SkipTest("could not export a PCK with this Godot build")
        shutil.copy(cls.editor, cls.game / "game.exe")
        if cls.editor_cli != cls.editor:
            shutil.copy(cls.editor_cli, cls.game / "game.console.exe")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def play(self) -> dict[str, str]:
        report = self.game / "report.txt"
        report.unlink(missing_ok=True)
        launcher = self.game / ("game.console.exe" if (self.game / "game.console.exe").is_file() else "game.exe")
        _run([launcher, "--headless", "--main-pack", "game.pck"], self.game, {"LOC_REPORT": str(report)})
        self.assertTrue(report.is_file(), "the game did not write its report (script error?)")
        values: dict[str, str] = {}
        last = ""
        for line in report.read_text(encoding="utf-8").splitlines():
            if re.match(r"^[a-z_]+=", line):
                last, value = line.split("=", 1)
                values[last] = value
            elif last:  # a value that itself contains line breaks (bilingual text)
                values[last] += "\n" + line
        return values

    def patch(self, locale: str = "de", bilingual: bool = False) -> None:
        pipeline = GodotTranslationPipeline.__new__(GodotTranslationPipeline)
        pipeline.bilingual = bilingual
        pipeline.bilingual_template = "{target}\n({source})"
        pipeline.pck_key, pipeline.font_path, pipeline.rtl_shaping, pipeline.last_harvest_count = "", "", True, 0
        profile = pipeline.profile_game(str(self.game))
        reader = PCKReader(profile.game_path)
        reader.open()
        by_key, unique = pipeline._harvest(reader, "auto", None)
        pipeline._apply_translations(by_key, unique, FAKE, locale, profile.major_version >= 4)
        pipeline._deploy(reader, profile, list(by_key.values()), locale, "overlay")

    def test_patch_translates_ui_and_keyed_tables(self) -> None:
        for leftover in ("override.cfg", "godotlocalizer_patch.pck", "godotlocalizer_bootstrap.gd"):
            (self.game / leftover).unlink(missing_ok=True)
        baseline = self.play()
        self.assertEqual(baseline["key_exit"], "Exit Game")

        self.patch("de")
        patched = self.play()
        self.assertEqual(patched["locale"], "de")
        self.assertEqual(patched["label"], "Hallo Welt")  # text of a (binary) scene node
        self.assertEqual(patched["button"], "Spiel starten")
        self.assertEqual(patched["key_exit"], "Spiel beenden")  # key lookup through the rebuilt table
        self.assertEqual(patched["key_start"], "Das Abenteuer beginnen")
        self.assertEqual(patched["key_score"], "Punkte: 42")

    def test_exported_package_installs_into_a_clean_copy_and_works(self) -> None:
        from src.patcher.package import export_package, install_package, write_sidecar

        for leftover in ("override.cfg", "godotlocalizer_patch.pck", "godotlocalizer_bootstrap.gd", "godotlocalizer_patch.json"):
            (self.game / leftover).unlink(missing_ok=True)
        self.patch("de")
        write_sidecar(self.game, "de", True, (4, 0, 0), self.game / "game.pck")
        archive = export_package(self.game, self.game.parent / "share" / "patch.zip", "game")

        OverlayPatcher(self.game).remove_patch()  # the recipient starts from the untouched game
        self.assertEqual(self.play()["key_exit"], "Exit Game")
        result = install_package(archive, self.game, self.game / "game.pck")
        self.assertEqual(result.warnings, [])
        patched = self.play()
        self.assertEqual((patched["label"], patched["key_exit"]), ("Hallo Welt", "Spiel beenden"))

    def test_bilingual_mode_never_breaks_formatted_strings(self) -> None:
        for leftover in ("override.cfg", "godotlocalizer_patch.pck", "godotlocalizer_bootstrap.gd", "godotlocalizer_patch.json"):
            (self.game / leftover).unlink(missing_ok=True)
        self.patch("de", bilingual=True)
        report = self.play()  # the game formats tr("MENU_SCORE") % 42 and would crash on a duplicated %d
        self.assertEqual(report["key_score"], "Punkte: 42")
        self.assertEqual(report["label"], "Hallo Welt\n(Hello World)")
        self.assertEqual(report["key_exit"], "Spiel beenden\n(Exit Game)")

    def test_clean_revert_restores_the_game(self) -> None:
        self.patch("de")
        OverlayPatcher(self.game).remove_patch()
        self.assertEqual(self.play()["key_exit"], "Exit Game")


class TestGodot4Engine(_EngineTests, unittest.TestCase):
    env_var = "GODOT4_BIN"
    files: ClassVar[dict[str, str]] = G4_FILES

    def test_autoload_prepend_runs_before_game_autoloads(self) -> None:
        self.patch("de")
        order = self.play()["root_children"].split(",")
        self.assertIn("GodotLocalizerBootstrap", order)
        # Measured: [autoload_prepend] works from Godot 4.6 on (4.4.1 and 4.5.1 run the bootstrap last).
        if self.engine_version() >= (4, 6):
            self.assertLess(order.index("GodotLocalizerBootstrap"), order.index("GameState"))

    @classmethod
    def engine_version(cls) -> tuple[int, int]:
        import re

        out = _run([cls.editor_cli, "--version"], cls.game)
        match = re.search(r"(\d+)\.(\d+)", out)
        return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


class TestGodot3Engine(_EngineTests, unittest.TestCase):
    env_var = "GODOT3_BIN"
    files: ClassVar[dict[str, str]] = G3_FILES

    def test_bootstrap_runs_even_without_prepend_support(self) -> None:
        self.patch("de")
        self.assertIn("GodotLocalizerBootstrap", self.play()["root_children"])


if __name__ == "__main__":
    unittest.main()
