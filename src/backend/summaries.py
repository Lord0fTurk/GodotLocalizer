"""Plain-dict payloads the backend hands to QML (completion card, font report, GDRE release)."""

from __future__ import annotations

from typing import Any

from src.backend.game_files import has_game_executable
from src.core.external.gdre_download import GdreRelease
from src.core.font_engine.glyph_coverage import GlyphCoverageChecker


def completion_summary(session: Any, game_path: str, game_name: str, target_lang: str) -> dict[str, Any]:
    """Result card data for a finished translation run."""
    counts = session.counts()
    total = len(session.rows)
    translated = session.shippable_count
    rate = round((translated / total) * 100.0, 1) if total > 0 else 100.0
    return {
        "total": total,
        "translated": translated,
        "target": target_lang,
        "total_harvested": total,
        "translated_count": translated,
        "failed_count": counts.get("failed", 0),
        "rejected_count": counts.get("rejected", 0) + counts.get("invalid", 0),
        "unchanged_count": counts.get("unchanged", 0),
        "success_rate": f"{rate}%",
        "game_name": game_name,
        "game_path": game_path,
        "target_lang": target_lang,
        "has_exe": has_game_executable(game_path),
    }


def font_report(font_path: str, language: str) -> dict[str, Any]:
    """Glyph coverage of a font file for the target language (never raises)."""
    try:
        report = GlyphCoverageChecker.check_font_file(font_path, language)
        return {
            "covered": report.covered_count,
            "total": report.total_required,
            "ratio": round(report.coverage_ratio * 100, 1),
            "is_ok": report.is_fully_covered,
            "missing": "".join(report.missing_chars[:20]),
            "error": "",
        }
    except Exception as e:
        return {"covered": 0, "total": 0, "ratio": 0.0, "is_ok": False, "missing": "", "error": str(e)}


def release_payload(release: GdreRelease) -> dict[str, Any]:
    """What the GDRE consent dialog shows."""
    return {
        "tag": release.tag,
        "file": release.asset_name,
        "sizeMb": round(release.size / (1024 * 1024), 1),
        "source": release.page,
        "sha256": release.sha256,
        "license": release.license,
    }
