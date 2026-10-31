"""CSV translation sheets: Excel quirks (BOM, ';', formulas) and round trips through a review session."""

import csv
import tempfile
import unittest
from pathlib import Path

from src.core.pipeline.review_session import STATUS_EDITED, STATUS_INVALID, ReviewSession, RunContext
from src.core.pipeline.sheet import export_sheet, import_sheet


def make_session() -> ReviewSession:
    session = ReviewSession(context=RunContext(game_root=Path("."), target_lang="tr"))
    session.add_row("a", "Start Game", "Oyunu Başlat")
    session.add_row("b", "- Leave quietly", "")  # starts like a formula, untranslated
    session.add_row("c", "Score: %d points", "Puan: %d puan")
    session.add_row("d", "Line one\nLine two", "Satır bir\nSatır iki")
    return session


class TestSheet(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "sheet.csv"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_export_is_excel_friendly_and_formula_safe(self) -> None:
        self.assertEqual(export_sheet(make_session(), self.path), 4)
        raw = self.path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "Excel needs a BOM to read UTF-8")
        rows = list(csv.reader(raw.decode("utf-8-sig").splitlines(keepends=True)))
        self.assertEqual(rows[0], ["source", "translation", "status"])
        self.assertEqual(rows[2][0], "'- Leave quietly")  # never starts with a formula character

    def test_round_trip_with_translator_edits_in_a_semicolon_sheet(self) -> None:
        export_sheet(make_session(), self.path)
        # Simulate a translator in a ';'-locale Excel: fills row 2, fixes row 1, breaks the placeholder of row 3.
        text = self.path.read_text(encoding="utf-8-sig")
        rows = list(csv.reader(text.splitlines(keepends=True)))
        rows[1][1] = "Oyuna Başla"
        rows[2][1] = "- Sessizce ayrıl"
        rows[3][1] = "Puan: yok"
        rows.append(["Not in the game", "Oyunda yok", "accepted"])
        with open(self.path, "w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream, delimiter=";").writerows(rows)

        session = make_session()
        result = import_sheet(session, self.path)
        self.assertEqual((result.updated, result.unchanged, result.unknown, result.invalid), (3, 1, 1, 1))
        by_source = {r.original: r for r in session.rows}
        self.assertEqual(by_source["Start Game"].translation, "Oyuna Başla")
        self.assertEqual(by_source["Start Game"].status, STATUS_EDITED)
        self.assertEqual(by_source["- Leave quietly"].translation, "- Sessizce ayrıl")  # guard removed
        self.assertEqual(by_source["Score: %d points"].status, STATUS_INVALID)  # Syntax Guard still applies
        self.assertEqual(by_source["Line one\nLine two"].translation, "Satır bir\nSatır iki")

    def test_sheet_without_the_expected_columns_is_rejected(self) -> None:
        self.path.write_text("id,text\n1,hello\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            import_sheet(make_session(), self.path)

    def test_empty_translations_do_not_wipe_existing_ones(self) -> None:
        self.path.write_text("source,translation\nStart Game,\n", encoding="utf-8")
        session = make_session()
        self.assertEqual(import_sheet(session, self.path).updated, 0)
        self.assertEqual(session.rows[0].translation, "Oyunu Başlat")


if __name__ == "__main__":
    unittest.main()
