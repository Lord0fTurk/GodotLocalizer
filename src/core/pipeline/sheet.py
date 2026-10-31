"""Translation sheets: hand the strings to a human translator as a spreadsheet and take the result back.

CSV is what every spreadsheet program opens. Two real-world traps are handled here:

* Excel only reads UTF-8 correctly with a byte-order mark, so sheets are written as ``utf-8-sig`` (and read back
  tolerantly, including the ``;`` separator that Excel uses in many locales);
* cells that start with ``= + - @`` would be executed as formulas, so they are prefixed with ``'`` on export
  and the guard is removed again on import.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from src.core.pipeline.review_session import STATUS_FAILED, STATUS_REJECTED, ReviewSession

COLUMNS = ("source", "translation", "status")
_FORMULA_STARTS = ("=", "+", "-", "@")
_DELIMITERS = ",;\t"


@dataclass(slots=True)
class ImportResult:
    """Outcome of :func:`import_sheet`."""

    updated: int = 0
    unchanged: int = 0
    unknown: int = 0  # sheet rows whose source text is not in the session
    invalid: int = 0  # edits that the Syntax Guard flagged (they are still applied so they can be fixed)


def _guard(text: str) -> str:
    return "'" + text if text.startswith(_FORMULA_STARTS) else text


def _unguard(text: str) -> str:
    return text[1:] if text.startswith("'") and text[1:].startswith(_FORMULA_STARTS) else text


def export_sheet(session: ReviewSession, path: str | Path, only_problems: bool = False) -> int:
    """Writes the session as a CSV sheet; returns the number of rows written."""
    rows = [r for r in session.rows if not only_problems or r.status in (STATUS_FAILED, STATUS_REJECTED)]
    with open(path, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow([_guard(row.original), _guard(row.translation), row.status])
    return len(rows)


def _detect_dialect(sample: str) -> type[csv.Dialect] | csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=_DELIMITERS)
    except csv.Error:
        return csv.excel


def import_sheet(session: ReviewSession, path: str | Path) -> ImportResult:
    """Applies the ``translation`` column to the rows with the same ``source`` text."""
    with open(path, encoding="utf-8-sig", newline="") as stream:
        text = stream.read()
    reader = csv.reader(text.splitlines(keepends=True), _detect_dialect(text[:4096]))
    header = [cell.strip().lower() for cell in next(reader, [])]
    if "source" not in header or "translation" not in header:
        raise ValueError("The sheet needs 'source' and 'translation' columns.")
    src_col, tr_col = header.index("source"), header.index("translation")

    by_source = {row.original: row.row_id for row in session.rows}
    result = ImportResult()
    for cells in reader:
        if len(cells) <= max(src_col, tr_col):
            continue
        source, translation = _unguard(cells[src_col]), _unguard(cells[tr_col])
        row_id = by_source.get(source)
        if row_id is None:
            result.unknown += 1
        elif not translation.strip() or session.rows[row_id].translation == translation:
            result.unchanged += 1
        else:
            edited = session.edit(row_id, translation)
            result.updated += 1
            result.invalid += bool(edited.issues)
    return result
