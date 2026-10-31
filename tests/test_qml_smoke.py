"""Loads the real QML settings view offscreen and exercises the GDRE consent dialog (in a clean process)."""

import subprocess
import sys
import unittest
from pathlib import Path

RUNNER = Path(__file__).with_name("qml_smoke_runner.py")


class TestQmlSmoke(unittest.TestCase):
    def test_settings_view_and_gdre_dialog(self) -> None:
        result = subprocess.run(
            [sys.executable, str(RUNNER)], capture_output=True, text=True, timeout=120, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
