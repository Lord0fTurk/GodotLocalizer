"""Extension-routed text harvesting and file reconstruction for Godot packages."""

from __future__ import annotations

import re
from collections.abc import Callable

from src.core.binary.gdc import GdcError, extract_string_constants, is_compiled_script
from src.core.binary.optimized_translation import rebuild, to_runtime_payload
from src.core.binary.rsrc import RSRCError, parse_rsrc
from src.core.pck.reader import PCKReader
from src.harvesters.base import HarvestedEntry, StringSourceType
from src.harvesters.binary_harvester import (
    OPTIMIZED_TYPES,
    harvest_binary_resource,
    parse_mo_catalog,
    parse_translation_resource,
)
from src.harvesters.data_harvester import (
    DATA_KINDS,
    KIND_CFG,
    KIND_TRES,
    KIND_TXT,
    KIND_YAML,
    apply_data_translations,
    harvest_data_file,
)
from src.harvesters.scene_harvester import SceneHarvester
from src.harvesters.script_harvester import ScriptHarvester
from src.harvesters.translation_harvester import TranslationHarvester
from src.parsers.dialogic import DialogicParser
from src.parsers.dialogue_manager import DialogueManagerParser
from src.patcher.bootstrap_script import godot_locale

# Strings like UI_START_GAME or menu.start are translation *keys*; translating them breaks lookups.
_TRANSLATION_KEY_RE = re.compile(r"^[A-Z0-9]+(?:[_.][A-Z0-9]+)+$")

KIND_TSCN = "tscn"
KIND_DIALOGUE = "dialogue"
KIND_DIALOGUE_TRES = "dialogue_tres"
KIND_DTL = "dtl"
KIND_D1_JSON = "d1_json"
KIND_JSON = "json"
KIND_CSV = "csv"
KIND_PO = "po"
KIND_SCN = "scn"
KIND_GD = "gd"
KIND_RES = "res"
KIND_TRANSLATION = "translation"
KIND_MO = "mo"

BINARY_KINDS = frozenset({KIND_SCN, KIND_RES, KIND_TRANSLATION, KIND_MO})
MAX_BINARY_BYTES = 32 * 1024 * 1024
MAX_DATA_BYTES = 16 * 1024 * 1024
# Licences and engine/export configuration share the plain-text extensions but carry no game text.
_NON_TEXT_FILES = frozenset({"export_presets.cfg", "override.cfg", "plugin.cfg", "engine.cfg", "requirements.txt", "robots.txt"})
_NON_TEXT_PREFIXES = ("license", "licence", "copying", "copyright", "readme", "thirdparty", "third_party", "authors")


def is_translation_key(text: str) -> bool:
    """True for identifier-style strings (UPPER_SNAKE / dotted caps) that must not be translated."""
    return bool(_TRANSLATION_KEY_RE.match(text.strip()))


def classify(res_path: str) -> str | None:
    """Maps a res:// path to a harvest kind, or None for files that carry no harvestable text."""
    lowered = res_path.lower()
    if lowered.endswith(".translation"):
        return KIND_TRANSLATION
    if lowered.endswith(".mo") and "/.godot/" not in lowered:
        return KIND_MO
    if lowered.endswith(".gd"):
        return KIND_GD
    if lowered.endswith(".scn"):
        return KIND_SCN
    if lowered.endswith(".res") and "/.godot/imported/" not in lowered:
        return KIND_RES
    if lowered.endswith(".tscn"):
        return KIND_TSCN
    if lowered.endswith(".dialogue"):
        return KIND_DIALOGUE
    if ".dialogue-" in lowered and lowered.endswith(".tres"):
        return KIND_DIALOGUE_TRES
    if lowered.endswith(".dtl"):
        return KIND_DTL
    if lowered.endswith(".json") and "migrations" not in lowered and "/saves/" not in lowered:
        return KIND_D1_JSON if "timelines" in lowered else KIND_JSON
    if lowered.endswith(".csv"):
        return KIND_CSV
    if lowered.endswith(".po") and "/l10n/" not in lowered:
        return KIND_PO
    return _classify_data_file(lowered)


def _classify_data_file(lowered: str) -> str | None:
    """Plain-text data formats; plugin code and engine/export configuration never hold game text."""
    if lowered.startswith("res://addons/") or "/.godot/" in lowered:
        return None
    name = lowered.rsplit("/", 1)[-1]
    if name in _NON_TEXT_FILES or name.startswith(_NON_TEXT_PREFIXES):
        return None
    if lowered.endswith(".tres"):
        return KIND_TRES
    if lowered.endswith(".txt"):
        return KIND_TXT
    if lowered.endswith((".yaml", ".yml")):
        return KIND_YAML
    if lowered.endswith((".cfg", ".ini")):
        return KIND_CFG
    return None


def parse_json_keys(raw: str) -> tuple[str, ...]:
    """Splits the user's comma/space separated list of extra JSON key names."""
    return tuple(dict.fromkeys(part for part in raw.replace(",", " ").split() if part))


def _harvest_one(kind: str, text: str, res_path: str, json_keys: tuple[str, ...] = ()) -> list[HarvestedEntry]:
    if kind == KIND_TSCN:
        return SceneHarvester.harvest_tscn(text, res_path)
    if kind == KIND_DIALOGUE:
        return DialogueManagerParser.parse_content(text, res_path)
    if kind == KIND_DIALOGUE_TRES:
        return DialogueManagerParser.parse_compiled_tres(text, res_path)
    if kind == KIND_DTL:
        return DialogicParser.parse_dtl(text, res_path)
    if kind == KIND_D1_JSON:
        return DialogicParser.parse_d1_json(text, res_path)
    if kind == KIND_JSON:
        return DialogicParser.parse_generic_json(text, res_path, json_keys)
    if kind == KIND_CSV:
        return TranslationHarvester.harvest_csv(text, res_path)
    if kind == KIND_GD:
        return ScriptHarvester.harvest_gd(text, res_path)
    if kind in DATA_KINDS:
        return harvest_data_file(kind, text, res_path)
    return TranslationHarvester.harvest_po(text, res_path)


def harvest_decompiled_scripts(
    reader: PCKReader, scripts: dict[str, str], include_addons: bool = False
) -> list[HarvestedEntry]:
    """Strings from GDScript sources recovered from ``.gdc`` bytecode (runtime table only, never rewritten).

    Plugin code under ``res://addons/`` is skipped by default: its strings are editor tooling, not game text.
    """
    entries: list[HarvestedEntry] = []
    for res_path, source in scripts.items():
        if not include_addons and res_path.startswith("res://addons/"):
            continue
        if res_path in reader.entries:  # already harvested as plain text
            continue
        for entry in ScriptHarvester.harvest_gd(source, res_path):
            entry.metadata["decompiled"] = True
            entries.append(entry)
    return entries


def harvest_compiled_scripts_native(reader: PCKReader, include_addons: bool = False) -> list[HarvestedEntry]:
    """Strings straight from ``.gdc`` constant tables (Godot 4.3+), no GDRE Tools needed.

    Same destination as the GDRE route (runtime translation table only), but without call context, so
    developer log strings are filtered by shape alone. Older bytecode formats are skipped silently.
    """
    entries: list[HarvestedEntry] = []
    for res_path in reader.entries:
        if not res_path.endswith(".gdc") or (not include_addons and res_path.startswith("res://addons/")):
            continue
        data = reader.read_file_bytes(res_path)
        if not data or not is_compiled_script(data):
            continue
        source_file = res_path.removesuffix("c")
        try:
            strings = extract_string_constants(data)
        except GdcError:
            continue
        for index, text in enumerate(strings):
            if ScriptHarvester.is_player_text(text):
                entries.append(
                    HarvestedEntry(
                        key=f"{source_file}::C{index}::{text[:24]}",
                        source_text=text,
                        source_file=source_file,
                        source_type=StringSourceType.SCRIPT_CODE,
                        context="Compiled script constant",
                        metadata={"language": "gdscript", "native_gdc": True},
                    )
                )
    return entries


def _harvest_mo(raw: bytes, res_path: str) -> tuple[str, list[HarvestedEntry]] | None:
    """A ``.mo`` catalog; when its ``msgid`` is a key (``UI_START``) the ``msgstr`` is the text to translate."""
    parsed = parse_mo_catalog(raw, res_path)
    if parsed is None:
        return None
    for entry in parsed[1]:
        msgstr = str(entry.metadata.get("msgstr", ""))
        if is_translation_key(entry.source_text) and msgstr.strip() and not entry.metadata.get("plural_group"):
            entry.source_text = msgstr
    return parsed


def pick_source_locale(locales: dict[str, int], source_lang: str = "auto") -> str:
    """Chooses which translation locale holds the game's source text: requested, else English, else largest."""
    if not locales:
        return ""
    wanted = godot_locale(source_lang).lower() if source_lang and source_lang != "auto" else ""
    by_lower = {loc.lower(): loc for loc in locales}
    if wanted:
        for key, loc in by_lower.items():
            if key == wanted or key.split("_")[0] == wanted.split("_")[0]:
                return loc
    for key, loc in by_lower.items():
        if key.split("_")[0] == "en":
            return loc
    return max(locales, key=lambda loc: locales[loc])


def harvest_pack(
    reader: PCKReader,
    on_file: Callable[[], None] | None = None,
    source_lang: str = "auto",
    json_keys: tuple[str, ...] = (),
) -> list[HarvestedEntry]:
    """Harvests every text-bearing file; textures, audio and other assets are never read.

    ``json_keys`` are extra JSON field names whose string values count as text (game-specific schemas).
    """
    entries: list[HarvestedEntry] = []
    translation_entries: list[HarvestedEntry] = []
    for res_path in reader.entries:
        kind = classify(res_path)
        if kind is None:
            continue
        if reader.entries[res_path].size > (MAX_BINARY_BYTES if kind in BINARY_KINDS else MAX_DATA_BYTES):
            continue
        if on_file:
            on_file()
        raw = reader.read_file_bytes(res_path)
        if not raw:
            continue
        if kind in (KIND_TRANSLATION, KIND_MO):
            parsed = (
                parse_translation_resource(raw, res_path) if kind == KIND_TRANSLATION else _harvest_mo(raw, res_path)
            )
            if parsed:
                translation_entries.extend(parsed[1])
            continue
        if kind in BINARY_KINDS:
            found = harvest_binary_resource(raw, res_path)
        else:
            found = _harvest_one(kind, raw.decode("utf-8-sig", errors="replace"), res_path, json_keys)
        entries.extend(e for e in found if e.source_text.strip() and not is_translation_key(e.source_text))

    sizes: dict[str, int] = {}
    for entry in translation_entries:
        locale = str(entry.metadata.get("locale", ""))
        sizes[locale] = sizes.get(locale, 0) + 1
    source_locale = pick_source_locale(sizes, source_lang)
    entries.extend(e for e in translation_entries if e.metadata.get("locale", "") == source_locale)
    return entries

def _rebuild(
    kind: str, text: str, lookup: dict[str, str], res_path: str, json_keys: tuple[str, ...] = ()
) -> str | None:
    if kind == KIND_TSCN:
        return SceneHarvester.apply_tscn_translations(text, lookup, res_path)
    if kind == KIND_DIALOGUE:
        return DialogueManagerParser.apply_translations(text, lookup, res_path)
    if kind == KIND_DIALOGUE_TRES:
        return DialogueManagerParser.apply_compiled_tres_translations(text, lookup, res_path)
    if kind == KIND_DTL:
        return DialogicParser.apply_dtl_translations(text, lookup, res_path)
    if kind == KIND_D1_JSON:
        return DialogicParser.apply_d1_json_translations(text, lookup, res_path)
    if kind == KIND_JSON:
        return DialogicParser.apply_generic_json_translations(text, lookup, res_path, json_keys)
    if kind in DATA_KINDS:
        return apply_data_translations(kind, text, lookup, res_path)
    return None  # CSV / PO are import sources: Godot reads their .translation output, see runtime table


def build_localized_files(
    reader: PCKReader, entries: list[HarvestedEntry], json_keys: tuple[str, ...] = ()
) -> dict[str, bytes]:
    """Rewrites every source file that has translated entries; returns res:// path -> new bytes."""
    by_file: dict[str, list[HarvestedEntry]] = {}
    for entry in entries:
        by_file.setdefault(entry.source_file, []).append(entry)

    localized: dict[str, bytes] = {}
    for res_path, file_entries in by_file.items():
        kind = classify(res_path)
        lookup = {e.key: e.translation for e in file_entries if e.translation}
        raw = reader.read_file_bytes(res_path) if kind and kind not in BINARY_KINDS and lookup else None
        if not raw or kind is None:
            continue
        rebuilt = _rebuild(kind, raw.decode("utf-8-sig", errors="replace"), lookup, res_path, json_keys)
        if rebuilt is not None:
            localized[res_path] = rebuilt.encode("utf-8")
    return localized


def build_runtime_messages(entries: list[HarvestedEntry]) -> dict[str, str]:
    """Source-text and translation-key lookup table registered with Godot's TranslationServer.

    Control nodes translate their text through ``tr()`` automatically, so a message keyed by the
    original text localizes hardcoded UI even when the scene itself is a binary ``.scn``.
    """
    messages: dict[str, str] = {}
    for entry in entries:
        if not entry.translation or entry.metadata.get("plural_form"):
            continue  # a plural form shares its msgid with the singular and must not overwrite it
        messages[entry.source_text.strip()] = entry.translation
        key = entry.metadata.get("translation_key")
        if entry.source_type is StringSourceType.TRANSLATION and key:
            messages[str(key)] = entry.translation
    return messages


_MAX_PLURAL_FORMS = 6  # Arabic has six; Godot only reads the forms its locale rule selects


def build_runtime_extras(entries: list[HarvestedEntry]) -> dict[str, list[list[object]]]:
    """Context-bound and plural messages for the runtime table (Godot 4's ``tr(text, context)`` / ``tr_n``).

    ``contextual`` rows are ``[msgctxt, msgid, translation]``. ``plurals`` rows are ``[msgctxt, msgid, forms]``:
    only the singular and the plural form are translated, so the plural form fills every further slot, which
    keeps languages with more plural categories (Russian, Arabic) from falling back to the source text.
    """
    contextual: list[list[object]] = []
    groups: dict[str, dict[str, object]] = {}
    for entry in entries:
        if not entry.translation:
            continue
        meta = entry.metadata
        context, msgid = str(meta.get("msgctxt", "")), str(meta.get("translation_key", entry.source_text.strip()))
        if meta.get("plural_group"):
            group = groups.setdefault(str(meta["plural_group"]), {"context": context, "msgid": msgid, "forms": {}})
            group["forms"][int(meta.get("plural_form", 0))] = entry.translation  # type: ignore[index]
        elif context:
            contextual.append([context, msgid, entry.translation])

    plurals: list[list[object]] = []
    for group in groups.values():
        forms: dict[int, str] = group["forms"]  # type: ignore[assignment]
        singular, plural = forms.get(0), forms.get(1)
        if singular is None and plural is None:
            continue
        row = [singular or plural or ""]
        if plural:
            row += [plural] * (_MAX_PLURAL_FORMS - 1)
        plurals.append([group["context"], group["msgid"], row])
    extras: dict[str, list[list[object]]] = {"contextual": contextual, "plurals": plurals}
    return {name: rows for name, rows in extras.items() if rows}


def build_optimized_translations(
    reader: PCKReader, entries: list[HarvestedEntry], target_locale: str
) -> list[dict[str, object]]:
    """Rebuilds each harvested OptimizedTranslation with translated values for ``target_locale``.

    The result is shipped as data and re-created by the bootstrap at runtime, so the engine never
    needs the (hashed-away) source keys.
    """
    by_file: dict[str, dict[str, str]] = {}
    for entry in entries:
        if entry.metadata.get("optimized") and entry.translation:
            by_file.setdefault(entry.source_file, {})[entry.source_text.strip()] = entry.translation

    payloads: list[dict[str, object]] = []
    for res_path, lookup in by_file.items():
        raw = reader.read_file_bytes(res_path)
        parsed = parse_rsrc_tables(raw) if raw else None
        if not parsed:
            continue
        table, buckets, blob = parsed
        new_buckets, new_blob = rebuild(table, buckets, blob, lookup)
        payloads.append(to_runtime_payload(table, new_buckets, new_blob, godot_locale(target_locale)))
    return payloads


def parse_rsrc_tables(raw: bytes) -> tuple[list[int], list[int], bytes] | None:
    """(hash_table, bucket_table, strings) of an OptimizedTranslation resource, else None."""
    try:
        main = parse_rsrc(raw).main
    except RSRCError:
        return None
    props = main.properties
    table, buckets, blob = props.get("hash_table"), props.get("bucket_table"), props.get("strings")
    if main.type in OPTIMIZED_TYPES and isinstance(table, list) and isinstance(buckets, list) and isinstance(blob, bytes):
        return table, buckets, blob
    return None
