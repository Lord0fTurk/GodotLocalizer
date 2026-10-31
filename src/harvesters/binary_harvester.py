"""Harvester for Godot binary resources: ``.scn`` scenes, ``.res`` resources and ``.translation`` tables."""

from __future__ import annotations

from typing import Any

from src.core.binary.optimized_translation import OptimizedTranslationError, read_elements
from src.core.binary.rsrc import RSRCError, RSRCFile, parse_rsrc
from src.harvesters.base import HarvestedEntry, StringSourceType
from src.harvesters.scene_harvester import SceneHarvester

# SceneState packing (scene/resources/packed_scene.{h,cpp})
NAME_INDEX_BITS = 18
NAME_MASK = (1 << NAME_INDEX_BITS) - 1
FLAG_PROP_NAME_MASK = (1 << 30) - 1
TYPE_INSTANTIATED = 0x7FFFFFFF
NODE_HEADER_INTS = 6  # parent, owner, type, name|index<<18, instance, property count; then pairs, group count, groups

_GENERIC_FIELDS = frozenset({
    "text", "bio", "notes", "description", "title", "subtitle", "caption", "prompt", "message",
    "dialogue", "line", "label", "display_name", "tooltip_text", "placeholder_text",
})
_MAX_RESOURCE_DEPTH = 12
# The hashed translation class is called PHashTranslation in Godot 3 and OptimizedTranslation in Godot 4.
OPTIMIZED_TYPES = frozenset({"OptimizedTranslation", "PHashTranslation"})


is_text_property = SceneHarvester.is_text_property


def _entry(source_file: str, where: str, prop: str, text: str, context: str, **meta: Any) -> HarvestedEntry:
    return HarvestedEntry(
        key=f"{source_file}::{where}::{prop}",
        source_text=text,
        source_file=source_file,
        source_type=StringSourceType.SCENE_UI,
        context=context,
        metadata={"binary": True, "property": prop, **meta},
    )


def _scene_entries(bundled: dict[Any, Any], source_file: str) -> list[HarvestedEntry] | None:
    """Reads nodes from a PackedScene ``_bundled`` dictionary; None when the layout does not add up."""
    names, variants, nodes = bundled.get("names"), bundled.get("variants"), bundled.get("nodes")
    count = bundled.get("node_count")
    if not (isinstance(names, list) and isinstance(variants, list) and isinstance(nodes, list) and isinstance(count, int)):
        return None

    entries: list[HarvestedEntry] = []
    paths: list[str] = []
    idx = 0
    for _ in range(count):
        if idx + NODE_HEADER_INTS > len(nodes):
            return None
        parent, _owner, type_idx, name_raw, _instance, prop_count = nodes[idx: idx + NODE_HEADER_INTS]
        idx += NODE_HEADER_INTS
        if prop_count < 0 or idx + prop_count * 2 + 1 > len(nodes) or (name_raw & NAME_MASK) >= len(names):
            return None
        name = str(names[name_raw & NAME_MASK])
        node_type = str(names[type_idx]) if 0 <= type_idx < len(names) else "Node"
        parent_path = paths[parent] if 0 <= parent < len(paths) else ""
        node_path = f"{parent_path}/{name}" if parent_path else name
        paths.append(node_path)

        for _p in range(prop_count):
            prop_idx, value_idx = nodes[idx] & FLAG_PROP_NAME_MASK, nodes[idx + 1]
            idx += 2
            if prop_idx >= len(names) or not 0 <= value_idx < len(variants):
                return None
            prop, value = str(names[prop_idx]), variants[value_idx]
            if is_text_property(prop) and isinstance(value, str) and value.strip():
                entries.append(_entry(source_file, node_path, prop, value, f"{node_type} ({node_path})", node_path=node_path))
        group_count = nodes[idx]
        idx += 1 + group_count
        if group_count < 0 or idx > len(nodes):
            return None
    return entries if idx == len(nodes) else None


def _walk(value: Any, where: str, source_file: str, out: list[HarvestedEntry], depth: int = 0) -> None:
    if depth > _MAX_RESOURCE_DEPTH:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _walk_item(str(key), item, where, source_file, out, depth)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk(item, f"{where}[{index}]", source_file, out, depth + 1)


def _walk_item(key: str, item: Any, where: str, source_file: str, out: list[HarvestedEntry], depth: int) -> None:
    here = f"{where}.{key}" if where else key
    if isinstance(item, str) and item.strip() and (key in _GENERIC_FIELDS or is_text_property(key)):
        out.append(_entry(source_file, here, key, item, here))
    else:
        _walk(item, here, source_file, out, depth + 1)


def _resource_entries(rsrc: RSRCFile, source_file: str) -> list[HarvestedEntry]:
    out: list[HarvestedEntry] = []
    for res in rsrc.resources:
        _walk(res.properties, res.path, source_file, out)
    return out


def harvest_binary_resource(data: bytes, source_file: str) -> list[HarvestedEntry]:
    """Text from a binary ``.scn`` / ``.res``; unreadable or unsupported files yield nothing."""
    try:
        rsrc = parse_rsrc(data)
    except RSRCError:
        return []
    main = rsrc.main
    bundled = main.properties.get("_bundled") if main.type == "PackedScene" else None
    if isinstance(bundled, dict):
        entries = _scene_entries(bundled, source_file)
        if entries is not None:
            return entries
    return _resource_entries(rsrc, source_file)


def parse_translation_resource(data: bytes, source_file: str) -> tuple[str, list[HarvestedEntry], dict[str, Any]] | None:
    """(locale, entries, raw tables) for a ``.translation`` file, or None if it is not one we can read.

    Plain ``Translation`` files keep their keys; ``OptimizedTranslation`` files only expose values
    (keys are hashed away), so their entries are keyed by table position.
    """
    try:
        main = parse_rsrc(data).main
    except RSRCError:
        return None
    # Godot omits properties that equal their default, and Translation.locale defaults to "en".
    locale = str(main.properties.get("locale", "en"))
    if main.type in OPTIMIZED_TYPES:
        return _optimized_entries(main.properties, locale, source_file)
    if main.type == "Translation":
        return locale, _plain_entries(main.properties.get("messages"), locale, source_file), {}
    return None


def _optimized_entries(props: dict[str, Any], locale: str, source_file: str) -> tuple[str, list[HarvestedEntry], dict[str, Any]] | None:
    table, buckets, blob = props.get("hash_table"), props.get("bucket_table"), props.get("strings")
    if not (isinstance(table, list) and isinstance(buckets, list) and isinstance(blob, bytes)):
        return None
    try:
        elements = read_elements(table, buckets, blob)
    except OptimizedTranslationError:
        return None
    entries = [
        HarvestedEntry(
            key=f"{source_file}::#{element.key_hash & 0xFFFFFFFF:08x}",
            source_text=element.text,
            source_file=source_file,
            source_type=StringSourceType.TRANSLATION,
            context=f"OptimizedTranslation ({locale})",
            metadata={"binary": True, "locale": locale, "optimized": True},
        )
        for element in elements
        if element.text.strip()
    ]
    return locale, entries, {"hash_table": table, "bucket_table": buckets, "strings": blob}


def _plain_entries(messages: Any, locale: str, source_file: str) -> list[HarvestedEntry]:
    pairs: list[tuple[str, str]] = []
    if isinstance(messages, dict):
        for key, value in messages.items():
            msgid = key[-1] if isinstance(key, (tuple, list)) and key else key  # (context, msgid) keys
            text = value[0] if isinstance(value, list) and value else value
            if isinstance(msgid, str) and isinstance(text, str):
                pairs.append((msgid, text))
    elif isinstance(messages, list):
        pairs = [(str(a), str(b)) for a, b in zip(messages[0::2], messages[1::2])]
    return [
        HarvestedEntry(
            key=f"{source_file}::{msgid}",
            source_text=text,
            source_file=source_file,
            source_type=StringSourceType.TRANSLATION,
            context=f"Translation ({locale}) ID: {msgid}",
            metadata={"binary": True, "locale": locale, "translation_key": msgid},
        )
        for msgid, text in pairs
        if text.strip()
    ]
