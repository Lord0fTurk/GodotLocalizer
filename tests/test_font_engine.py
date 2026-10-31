"""Unit tests for font glyph coverage and analysis."""

import unittest
from pathlib import Path

from src.core.font_engine.glyph_coverage import GlyphCoverageChecker


class TestFontEngine(unittest.TestCase):
    """Verifies font CMAP scanning and character set coverage calculation."""

    def test_arial_coverage(self) -> None:
        """Verifies glyph coverage of Windows standard Arial font for Turkish and German."""
        font_path = Path("C:/Windows/Fonts/arial.ttf")
        if not font_path.exists():
            self.skipTest("Arial font not found on host machine.")

        tr_report = GlyphCoverageChecker.check_font_file(font_path, lang_code="tr")
        self.assertTrue(tr_report.is_fully_covered)
        self.assertEqual(tr_report.coverage_ratio, 1.0)
        self.assertEqual(len(tr_report.missing_chars), 0)

        de_report = GlyphCoverageChecker.check_font_file(font_path, lang_code="de")
        self.assertTrue(de_report.is_fully_covered)

    def test_non_existent_font_handling(self) -> None:
        """Verifies that attempting to check a missing font file raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            GlyphCoverageChecker.check_font_file("C:/bogus_non_existent_font.ttf")


if __name__ == "__main__":
    unittest.main()
