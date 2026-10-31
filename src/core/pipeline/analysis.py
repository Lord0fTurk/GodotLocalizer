"""Pre-flight analysis of a game package: decides what it can on its own and asks only about what is unclear.

Pressing Translate inspects the package first. Compiled scripts of an older format and encrypted packs are
detected here; JSON data files are classified field by field so the user is not asked technical questions
("is ``source`` text?") that they cannot answer:

* fields full of sentences are included automatically;
* a pair of parallel columns (``source`` / ``target``) is the game's own localization table: the English side is
  taken as the text to translate and the other side is recognised as an existing translation;
* short names (characters, songs, places), placeholders and ids are ignored;
* only the in-between cases are put to the user, each with a plain-language explanation and a recommendation.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field

from src.core.binary.gdc import is_compiled_script
from src.core.pck.reader import PCKReader
from src.core.pipeline.harvest import KIND_D1_JSON, KIND_JSON, classify
from src.parsers.dialogic import DialogicParser

_MAX_JSON_BYTES = 8 * 1024 * 1024
_MAX_WALK_DEPTH = 24
_MAX_DISTINCT = 300
_MAX_PAIR_KEYS = 6
_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_IDENTIFIER_RE = re.compile(r"^[\w./:\\-]+$")
_ENGLISH_WORDS = frozenset(
    "the and of to a in is you that it for on with as i he she they we are was this be at have not but your my "
    "his her them there will would can what when so if or an by from do me".split()
)
# Fields that are plainly not text even when they hold words.
_IGNORED_KEYS = frozenset({
    "id", "uid", "name_id", "path", "file", "filename", "scene", "script", "icon", "image", "sprite", "texture",
    "sound", "sfx", "music", "audio", "font", "color", "type", "kind", "class", "tag", "tags", "key", "version",
})

# Reasons shown to the user (translated in the UI as settings.json_reason_<code>).
REASON_PROSE = "prose"                    # sentences: included automatically
REASON_TABLE_SOURCE = "table_source"      # the original-language column of the game's own table: included
REASON_TABLE_DONE = "table_translated"    # the other column: an existing translation, left out
REASON_TABLE_UNCLEAR = "table_unclear"    # two parallel columns, cannot tell which is the original: ask
REASON_MAYBE = "maybe"                    # short phrases that may or may not be shown to the player: ask
ASK_REASONS = frozenset({REASON_TABLE_UNCLEAR, REASON_MAYBE})


@dataclass(frozen=True, slots=True)
class JsonKeyCandidate:
    """A JSON field the analysis found; ``selected`` is the recommendation."""

    key: str
    count: int
    sample: str
    reason: str = REASON_PROSE
    selected: bool = True

    @property
    def needs_question(self) -> bool:
        return self.reason in ASK_REASONS


@dataclass(slots=True)
class _KeyStats:
    total: int = 0
    prose: int = 0
    values: dict[str, None] = field(default_factory=dict)  # distinct prose values, insertion ordered

    def sample(self) -> str:
        return next(iter(self.values), "")


def looks_like_prose(value: str) -> bool:
    """Two or more words, mostly letters, and not an identifier, path or number list."""
    text = value.strip()
    if len(text) < 6 or text.startswith(("res://", "user://", "http")) or _IDENTIFIER_RE.match(text):
        return False
    words = _WORD_RE.findall(text)
    letters = sum(ch.isalpha() for ch in text)
    return len(words) >= 2 and letters / len(text) >= 0.6


def english_score(values: list[str]) -> float:
    """Share of common English function words among the tokens of up to 100 values (0 = clearly not English)."""
    tokens = [t for v in values[:100] for t in re.findall(r"[a-zA-Z']+", v.lower())]
    return sum(t in _ENGLISH_WORDS for t in tokens) / len(tokens) if tokens else 0.0


def _is_name_like(value: str) -> bool:
    """'Deceptacon - Le Tigre', 'Bay Wilde': a few capitalised words, no sentence structure."""
    words = value.replace("-", " ").split()
    return len(words) <= 4 and all(w[:1].isupper() or not w[:1].isalpha() for w in words) and not re.search(r"[.!?,]", value)


def _collect(
    node: object, key: str, stats: dict[str, _KeyStats], pairs: dict[tuple[str, str], int], depth: int = 0
) -> None:
    if depth > _MAX_WALK_DEPTH:
        return
    if isinstance(node, dict):
        prose_keys = sorted(k for k, v in node.items() if isinstance(v, str) and looks_like_prose(v))
        if 2 <= len(prose_keys) <= _MAX_PAIR_KEYS:
            for i, a in enumerate(prose_keys):
                for b in prose_keys[i + 1:]:
                    pairs[(str(a), str(b))] += 1
        for child_key, child in node.items():
            _collect(child, str(child_key), stats, pairs, depth + 1)
    elif isinstance(node, list):
        for child in node:
            _collect(child, key, stats, pairs, depth + 1)
    elif isinstance(node, str) and key:
        entry = stats[key]
        entry.total += 1
        if looks_like_prose(node):
            entry.prose += 1
            if len(entry.values) < _MAX_DISTINCT:
                entry.values.setdefault(node, None)


def _classify_key(key: str, entry: _KeyStats) -> JsonKeyCandidate | None:
    """Prose / maybe / ignore, from how sentence-like the distinct values are."""
    values = list(entry.values)
    if len(key) > 40 or looks_like_prose(key) or entry.prose / max(entry.total, 1) < 0.7 or len(values) < 5:
        return None
    if sum(_is_name_like(v) for v in values) / len(values) >= 0.5:
        return None  # characters, songs, places: nothing to translate sentence by sentence
    sentences = sum(len(v.split()) >= 4 for v in values) / len(values)
    if sentences >= 0.6:
        return JsonKeyCandidate(key, entry.prose, entry.sample(), REASON_PROSE, True)
    if entry.prose >= 15:
        return JsonKeyCandidate(key, entry.prose, entry.sample(), REASON_MAYBE, False)
    return None


def _resolve_pairs(
    found: dict[str, JsonKeyCandidate], stats: dict[str, _KeyStats], pairs: dict[tuple[str, str], int]
) -> None:
    """Two fields that co-occur in the same objects are parallel columns of a localization table."""
    for (a, b), together in sorted(pairs.items(), key=lambda item: -item[1]):
        if a not in found or b not in found or found[a].reason == REASON_TABLE_DONE or found[b].reason == REASON_TABLE_DONE:
            continue
        if together < 0.8 * min(stats[a].prose, stats[b].prose):
            continue
        ea, eb = english_score(list(stats[a].values)), english_score(list(stats[b].values))
        if ea >= 0.1 and ea >= 2 * eb:
            original, done = a, b
        elif eb >= 0.1 and eb >= 2 * ea:
            original, done = b, a
        else:
            for key in (a, b):
                found[key] = JsonKeyCandidate(key, found[key].count, found[key].sample, REASON_TABLE_UNCLEAR, False)
            continue
        found[original] = JsonKeyCandidate(original, found[original].count, found[original].sample, REASON_TABLE_SOURCE, True)
        found[done] = JsonKeyCandidate(done, found[done].count, found[done].sample, REASON_TABLE_DONE, False)


def find_json_key_candidates(
    reader: PCKReader, known_fields: frozenset[str] | None = None, min_count: int = 5
) -> list[JsonKeyCandidate]:
    """JSON fields (not yet harvested) that carry player text, with a recommendation each; largest first."""
    known = frozenset(known_fields or ()) | frozenset(DialogicParser.GENERIC_JSON_FIELDS) | _IGNORED_KEYS
    stats: dict[str, _KeyStats] = defaultdict(_KeyStats)
    pairs: dict[tuple[str, str], int] = defaultdict(int)
    for res_path, entry in reader.entries.items():
        if classify(res_path) not in (KIND_JSON, KIND_D1_JSON) or entry.size > _MAX_JSON_BYTES:
            continue
        raw = reader.read_file_bytes(res_path)
        if not raw:
            continue
        try:
            data = json.loads(raw.decode("utf-8-sig", errors="replace"))
        except ValueError:
            continue
        _collect(data, "", stats, pairs)

    found = {
        key: candidate
        for key, entry in stats.items()
        if key not in known and entry.prose >= min_count and (candidate := _classify_key(key, entry)) is not None
    }
    _resolve_pairs(found, stats, pairs)
    return sorted(found.values(), key=lambda c: -c.count)


def count_unreadable_scripts(reader: PCKReader) -> int:
    """Compiled scripts in an older bytecode format that only GDRE Tools can decompile."""
    total = 0
    for res_path, entry in reader.entries.items():
        if res_path.endswith(".gdc") and not res_path.startswith("res://addons/"):
            head = reader.read_file_bytes(res_path) if entry.size >= 12 else None
            if head is not None and not is_compiled_script(head):
                total += 1
    return total
