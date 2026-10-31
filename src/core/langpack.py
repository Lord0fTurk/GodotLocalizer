"""External language packs: games that keep their text in JSON files next to the executable.

Some Godot games do not translate through the engine's tables at all. They ship ``external/data/**.json``
(cards, items, dialogue ...) plus optional ``external/mods/lang_<code>/`` folders; the game loads the pack of the
language chosen in its own menu and overrides the English values with it. A patch that only changes the ``.pck``
cannot reach that text, and the game resets the engine locale from its own settings besides.

The format is not fixed by Godot, so nothing is hard-coded about one game: the schema is *learned* from the
packs the game already ships. Comparing an official pack with the base data tells which top-level properties a
pack carries and which JSON keys hold text (``card_name``, ``dialogue_option_bbcode`` ...). A new pack is then
written with the same shape for the target language. Games without any official pack are not supported (there
is nothing to learn the format from).
"""

from __future__ import annotations

import copy
import json
import logging
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.version import __version__

logger = logging.getLogger(__name__)

PACK_PREFIX = "lang_"
UI_FILE = "ui_strings.json"
MOD_INFO = "mod_info.json"
MARKER = "godotlocalizer_langpack.json"
_SKIP_VALUES = ("res://", "external/", "user://")

Translate = Callable[[str], str]


class LangPackError(ValueError):
    """The game's language packs cannot be used (nothing to learn from, or a name collision)."""


@dataclass(slots=True)
class LangPackSchema:
    """What the game's official language packs look like."""

    root: Path                                   # the game's ``external`` folder
    references: list[str] = field(default_factory=list)       # codes of official packs, e.g. ['de', 'fr']
    properties: dict[str, set[str]] = field(default_factory=dict)  # category -> top-level properties packs carry
    text_keys: set[str] = field(default_factory=set)          # JSON keys whose string values are text
    ui_keys: list[str] = field(default_factory=list)          # English source strings of ui_strings.json

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def mods_dir(self) -> Path:
        return self.root / "mods"


def pack_code(target_lang: str) -> str:
    """Folder suffix the game expects for a language code: ``pt-BR`` -> ``pt_br``."""
    return target_lang.strip().lower().replace("-", "_")


def pack_dir(root: Path, target_lang: str) -> Path:
    return root / "mods" / f"{PACK_PREFIX}{pack_code(target_lang)}"


def find_language_root(game_dir: str | Path) -> Path | None:
    """The ``external`` folder when it holds the data files and at least one official language pack."""
    root = Path(game_dir) / "external"
    if not (root / "data").is_dir():
        return None
    return root if _official_packs(root) else None


def _official_packs(root: Path) -> list[Path]:
    mods = root / "mods"
    if not mods.is_dir():
        return []
    return sorted(
        p for p in mods.iterdir()
        if p.is_dir() and p.name.startswith(PACK_PREFIX) and (p / UI_FILE).is_file() and not (p / MARKER).exists()
    )


# --- learning the format ----------------------------------------------------------------------------------------

def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _is_text(value: Any) -> bool:
    return (
        isinstance(value, str) and bool(value.strip()) and any(c.isalpha() for c in value)
        and not value.startswith(_SKIP_VALUES)
    )


def _learn_keys(pack: Any, base: Any, key: str, found: set[str]) -> None:
    """Collects the JSON keys whose value differs from the base text (= was translated)."""
    if isinstance(pack, str) and isinstance(base, str):
        if pack != base and _is_text(base) and key != "object_id":
            found.add(key)
    elif isinstance(pack, dict) and isinstance(base, dict):
        for k in pack.keys() & base.keys():
            _learn_keys(pack[k], base[k], k, found)
    elif isinstance(pack, list) and isinstance(base, list):
        for p, b in zip(pack, base, strict=False):
            _learn_keys(p, b, key, found)


def learn_schema(root: Path) -> LangPackSchema:
    """Reads the official packs of a game and derives the pack format."""
    schema = LangPackSchema(root=root)
    packs = _official_packs(root)
    if not packs:
        raise LangPackError("The game has no official language pack to learn the format from.")
    ui: dict[str, None] = {}
    for pack in packs:
        schema.references.append(pack.name[len(PACK_PREFIX):])
        try:
            ui.update(dict.fromkeys(_read_json(pack / UI_FILE)))
        except (OSError, ValueError):
            logger.warning(f"Unreadable {pack / UI_FILE}; skipped.")
        for file in pack.rglob("*.json"):
            rel = file.relative_to(pack)
            if len(rel.parts) < 2:
                continue  # ui_strings.json, mod_info.json
            base_file = root / "data" / rel
            if not base_file.is_file():
                continue
            try:
                data, base = _read_json(file), _read_json(base_file)
            except (OSError, ValueError):
                continue
            props = data.get("properties", {}) if isinstance(data, dict) else {}
            schema.properties.setdefault(rel.parts[0], set()).update(props)
            _learn_keys(props, base.get("properties", {}), "properties", schema.text_keys)
    schema.ui_keys = list(ui)
    if not schema.text_keys:
        raise LangPackError("Could not tell which fields hold text from the game's language packs.")
    return schema


# --- harvesting and building ------------------------------------------------------------------------------------

def _project(value: Any, key: str, schema: LangPackSchema, translate: Translate) -> Any:
    """Copy of ``value`` where the strings under text keys went through ``translate``."""
    if isinstance(value, dict):
        return {k: _project(v, k, schema, translate) for k, v in value.items()}
    if isinstance(value, list):
        return [_project(v, key, schema, translate) for v in value]
    if isinstance(value, str) and key in schema.text_keys and _is_text(value):
        return translate(value)
    return copy.deepcopy(value)


def _base_files(schema: LangPackSchema) -> Iterator[tuple[Path, Path, dict[str, Any]]]:
    """(path relative to ``data``, absolute path, parsed JSON) for every base file a pack covers."""
    for category in sorted(schema.properties):
        for file in sorted((schema.data_dir / category).rglob("*.json")):
            try:
                data = _read_json(file)
            except (OSError, ValueError):
                logger.warning(f"Unreadable {file}; skipped.")
                continue
            if isinstance(data, dict) and isinstance(data.get("properties"), dict):
                yield file.relative_to(schema.data_dir), file, data


def harvest_strings(schema: LangPackSchema) -> list[str]:
    """Every distinct player-facing string of the base data, plus the pack's UI strings (stripped, in order)."""
    found: dict[str, None] = {}

    def collect(text: str) -> str:
        found.setdefault(text.strip())
        return text

    for rel, _file, data in _base_files(schema):
        keep = schema.properties[rel.parts[0]]
        _project({k: v for k, v in data["properties"].items() if k in keep}, "properties", schema, collect)
    for key in schema.ui_keys:
        if _is_text(key):
            found.setdefault(key.strip())
    return list(found)


def _wrap(text: str, translations: dict[str, str]) -> str:
    """Translation of ``text`` keeping its leading/trailing whitespace; unknown text stays as it is."""
    stripped = text.strip()
    done = translations.get(stripped)
    if not done:
        return text
    start = text[: len(text) - len(text.lstrip())]
    end = text[len(text.rstrip()):]
    return start + done.strip() + end


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent="\t") + "\n", encoding="utf-8")


def _mod_info(schema: LangPackSchema, code: str) -> dict[str, Any]:
    ref = schema.references[0]
    text = (schema.mods_dir / f"{PACK_PREFIX}{ref}" / MOD_INFO).read_text(encoding="utf-8-sig")
    for old, new in ((f"{PACK_PREFIX}{ref}", f"{PACK_PREFIX}{code}"), (f"({ref})", f"({code})"), (f"pack: {ref}.", f"pack: {code}.")):
        text = text.replace(old, new)
    info = json.loads(text)
    props = info.get("properties", {})
    props["mod_description"] = f"Language pack: {code}. Generated by GodotLocalizer {__version__}."
    return info


def build_pack(
    schema: LangPackSchema,
    translations: dict[str, str],
    target_lang: str,
    extra_ui: dict[str, str] | None = None,
) -> int:
    """Writes ``mods/lang_<code>/`` into the game; returns the number of translated strings it contains.

    ``translations`` maps stripped source text to its translation; ``extra_ui`` adds more UI strings
    (for example scene labels read from the ``.pck``).
    """
    code = pack_code(target_lang)
    if code in schema.references:
        raise LangPackError(f"The game already ships an official '{code}' language pack; refusing to replace it.")
    target = schema.mods_dir / f"{PACK_PREFIX}{code}"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    used: set[str] = set()

    def translate(text: str) -> str:
        out = _wrap(text, translations)
        if out != text:
            used.add(text.strip())
        return out

    for rel, _file, data in _base_files(schema):
        keep = schema.properties[rel.parts[0]]
        props = {k: v for k, v in data["properties"].items() if k in keep}
        projected = _project(props, "properties", schema, translate)
        projected = {"object_id": data["properties"].get("object_id", rel.stem), **projected}
        _write_json(target / rel, {"patch_data": {}, "properties": projected})

    ui_sources = dict.fromkeys(schema.ui_keys)
    ui_sources.update(dict.fromkeys(extra_ui or {}))
    ui = {key: _wrap(key, {**translations, **(extra_ui or {})}) for key in ui_sources}
    _write_json(target / UI_FILE, ui)
    _write_json(target / MOD_INFO, _mod_info(schema, code))
    _write_json(target / MARKER, {"tool": "GodotLocalizer", "tool_version": __version__, "locale": target_lang})
    translated = len(used) + sum(1 for k, v in ui.items() if v != k)
    logger.info(f"Language pack written to {target} ({translated} translated strings).")
    return translated


# --- status and removal -----------------------------------------------------------------------------------------

def installed_pack(game_dir: str | Path) -> dict[str, Any] | None:
    """Marker of the pack GodotLocalizer wrote into ``game_dir/external/mods``; None when there is none."""
    mods = Path(game_dir) / "external" / "mods"
    if not mods.is_dir():
        return None
    for folder in sorted(mods.glob(f"{PACK_PREFIX}*")):
        try:
            info = _read_json(folder / MARKER)
        except (OSError, ValueError):
            continue
        if isinstance(info, dict):
            return {**info, "folder": folder.name, "created": ""}
    return None


def remove_packs(game_dir: str | Path) -> int:
    """Deletes the packs written by this tool (never official ones); returns how many folders were removed."""
    mods = Path(game_dir) / "external" / "mods"
    removed = 0
    for folder in sorted(mods.glob(f"{PACK_PREFIX}*")) if mods.is_dir() else []:
        if (folder / MARKER).is_file():
            shutil.rmtree(folder)
            removed += 1
    return removed
