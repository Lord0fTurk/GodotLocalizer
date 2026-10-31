"""Pre-flight package analysis: what the user must be asked before translating."""

from __future__ import annotations

from typing import Any

from src.core.external.gdre import find_gdre
from src.core.pck.reader import PCKReader
from src.core.pipeline.analysis import count_unreadable_scripts, find_json_key_candidates
from src.core.pipeline.harvest import parse_json_keys

MAX_JSON_CANDIDATES = 12


def analyse_package(game_path: str, pck_key: str, json_text_keys: str, gdre_path: str) -> tuple[list[dict[str, Any]], int]:
    """JSON fields that look like text and the count of compiled scripts only GDRE Tools can read."""
    reader = PCKReader(game_path, key=pck_key)
    reader.open()
    known = frozenset(parse_json_keys(json_text_keys))
    candidates = [
        {
            "key": c.key, "count": c.count, "sample": c.sample[:90], "reason": c.reason,
            "selected": c.selected, "ask": c.needs_question,
        }
        for c in find_json_key_candidates(reader, known)[:MAX_JSON_CANDIDATES]
    ]
    legacy = 0 if find_gdre(gdre_path) else count_unreadable_scripts(reader)
    return candidates, legacy


def merge_json_keys(current: str, keys: list[str]) -> str:
    """The settings string after adding the approved JSON keys (existing ones keep their order)."""
    merged = list(parse_json_keys(current))
    merged += [k for k in keys if k not in merged]
    return ", ".join(merged)
