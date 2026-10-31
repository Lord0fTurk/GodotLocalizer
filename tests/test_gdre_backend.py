"""GDRE Tools bridge: output collection, noise filtering and the optional real-binary run (GDRE_TOOLS)."""

import os
import tempfile
import unittest
from pathlib import Path

from src.core.external.gdre import GdreBackend, GdreError, find_gdre
from src.harvesters.script_harvester import ScriptHarvester


class TestGdreBackend(unittest.TestCase):
    def test_collect_scripts_skips_autoconverted_and_requires_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(GdreError):
                GdreBackend.collect_scripts(root)
            (root / "scenes").mkdir()
            (root / "scenes" / "ui.gd").write_text('var t = "Game Over"\n', encoding="utf-8")
            (root / ".autoconverted").mkdir()
            (root / ".autoconverted" / "ui.gd").write_text("x", encoding="utf-8")
            self.assertEqual(list(GdreBackend.collect_scripts(root)), ["res://scenes/ui.gd"])

    def test_missing_executable_is_not_found(self) -> None:
        self.assertIsNone(find_gdre("C:/definitely/not/here/gdre_tools.exe") if not os.environ.get("GDRE_TOOLS") else None)

    def test_log_lines_are_not_harvested(self) -> None:
        code = 'print("INFO: loaded chat")\nvar a = "ERROR: could not load"\nvar b = "Return to menu"\n'
        texts = [e.source_text for e in ScriptHarvester.harvest_gd(code, "res://a.gd")]
        self.assertEqual(texts, ["Return to menu"])

    def test_developer_messages_and_identifiers_are_not_harvested(self) -> None:
        code = (
            'var a = "clearing invalid equipped ability path"\n'
            'var b = "act%d_boss"\n'
            '_log("Something happened here")\n'
            'dialog("Welcome, traveler")\n'
            'var c = "Achievement Unlocked!"\n'
            'var d = "wait for it!"\n'
        )
        texts = [e.source_text for e in ScriptHarvester.harvest_gd(code, "res://a.gd")]
        self.assertEqual(texts, ["Welcome, traveler", "Achievement Unlocked!", "wait for it!"])


@unittest.skipUnless(os.environ.get("GDRE_TOOLS") and os.environ.get("GDRE_SAMPLE_PCK"), "needs GDRE_TOOLS and GDRE_SAMPLE_PCK")
class TestRealGdre(unittest.TestCase):
    def test_decompiles_a_real_pack(self) -> None:
        exe = find_gdre()
        self.assertIsNotNone(exe)
        scripts = GdreBackend(exe).decompile_scripts(os.environ["GDRE_SAMPLE_PCK"])
        self.assertTrue(scripts)
        self.assertTrue(all(p.startswith("res://") and p.endswith(".gd") for p in scripts))


if __name__ == "__main__":
    unittest.main()
