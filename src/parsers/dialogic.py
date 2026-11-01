"""Parser and text replacer for Dialogic 1.x and 2.x narrative timelines."""

import json
import re
from collections.abc import Iterable, Iterator
from typing import Any

from src.harvesters.base import HarvestedEntry, StringSourceType


class DialogicParser:
    """Parses Dialogic 2.x text timelines (.dtl) and Dialogic 1.x JSON timelines."""

    # Dialogic 2.x timeline grammar
    D2_COMMANDS = frozenset({
        "join", "leave", "update", "label", "jump", "return", "if", "elif", "else", "set", "do", "call",
    })
    D2_TEXT_TAGS = frozenset({
        "b", "i", "u", "s", "code", "center", "left", "right", "fill", "color", "bgcolor", "fgcolor", "font",
        "font_size", "outline_size", "outline_color", "url", "img", "hint", "lang", "p", "wave", "shake",
        "tornado", "rainbow", "pulse", "fade", "pause", "speed", "nl", "n", "br", "mood", "portrait",
    })
    D2_CHOICE_PATTERN = re.compile(
        r"^(?P<indent>\t*|\s*)-\s*(?P<prompt>[^\n\r\[]+)(?P<extra>.*)$"
    )
    _D2_SPEAKER = re.compile(
        r"^\s*(?P<speaker>\{\w+\}|\?+|[\w'\-]+(?: [\w'\-]+){0,2})\s*(?:\([^)]*\))?\s*:(?!:)\s*(?P<text>\S.*)$"
    )
    _D2_TAG = re.compile(r"^\[\s*/?([A-Za-z_]+)")
    _D2_TEXT_SUFFIX = re.compile(r"(?:\s+#id:\S+)?\s*\\?\s*$")

    GENERIC_JSON_FIELDS: tuple[str, ...] = (
        "text",
        "bio",
        "notes",
        "description",
        "title",
        "subtitle",
        "caption",
        "prompt",
        "message",
    )

    @classmethod
    def parse_dtl(cls, content: str, source_file: str = "timeline.dtl") -> list[HarvestedEntry]:
        """Harvests dialogue, narration and choices from Dialogic 2.x (.dtl) timelines."""
        entries: list[HarvestedEntry] = []
        for line_num, line in enumerate(content.splitlines(), start=1):
            found = cls._classify_dtl_line(line)
            if found is None:
                continue
            kind, speaker, start, end = found
            key = f"{source_file}::L{line_num}::{speaker}"
            entries.append(HarvestedEntry(
                key=key,
                source_text=line[start:end].strip(),
                source_file=source_file,
                source_type=StringSourceType.NARRATIVE,
                context="Choice" if kind == "choice" else speaker,
                line_number=line_num,
                metadata={"framework": "dialogic2", "kind": "choice" if kind == "choice" else "dialogue"},
            ))
        return entries

    @classmethod
    def _classify_dtl_line(cls, line: str) -> tuple[str, str, int, int] | None:
        """(kind, speaker, text_start, text_end) of a translatable line; None for commands and blanks."""
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return None
        choice = cls.D2_CHOICE_PATTERN.match(line)
        if choice:
            prompt = choice.group("prompt")
            if not prompt.strip():
                return None
            return "choice", "choice", choice.start("prompt"), choice.start("prompt") + len(prompt.rstrip())
        if cls._is_dtl_command(stripped):
            return None
        end = cls._text_end(line)
        speaker_match = None if stripped.startswith("[") else cls._D2_SPEAKER.match(line[:end])
        if speaker_match:
            return "text", speaker_match.group("speaker"), speaker_match.start("text"), end
        start = len(line) - len(line.lstrip())
        if not any(ch.isalpha() for ch in line[start:end]):
            return None
        return "text", "Narrator", start, end

    @classmethod
    def _is_dtl_command(cls, stripped: str) -> bool:
        """Event lines: keyword events (join, jump, if ...) and [shortcode] events (wait, music ...)."""
        if stripped.startswith("["):
            tag = cls._D2_TAG.match(stripped)
            return not (tag and tag.group(1).lower() in cls.D2_TEXT_TAGS)
        return re.split(r"[\s:]", stripped, maxsplit=1)[0] in cls.D2_COMMANDS

    @classmethod
    def _text_end(cls, line: str) -> int:
        """End of the visible text: a trailing ``#id:...`` marker and a line-continuation backslash stay out."""
        match = cls._D2_TEXT_SUFFIX.search(line)
        return match.start() if match else len(line)

    @classmethod
    def apply_dtl_translations(cls, content: str, translations: dict[str, str], source_file: str) -> str:
        """Replaces translated dialogue/choice text in a Dialogic 2 timeline, preserving everything else."""
        eol = "\r\n" if "\r\n" in content else "\n"
        out: list[str] = []
        for line_num, line in enumerate(content.splitlines(), start=1):
            found = cls._classify_dtl_line(line)
            if found is not None:
                _kind, speaker, start, end = found
                translated = translations.get(f"{source_file}::L{line_num}::{speaker}")
                if translated:
                    lead = line[start:end]
                    gap = lead[: len(lead) - len(lead.lstrip())]
                    line = line[:start] + gap + translated + line[end:]
            out.append(line)
        trailing = eol if content.endswith(("\n", "\r")) else ""
        return eol.join(out) + trailing

    @classmethod
    def apply_d1_json_translations(cls, original_content: str, translations: dict[str, str], source_file: str) -> str:
        """Writes translated text/choice values back into a Dialogic 1.x timeline JSON."""
        try:
            data = json.loads(original_content.lstrip("﻿"))
        except json.JSONDecodeError:
            return original_content
        events = data.get("events", []) if isinstance(data, dict) else data
        if not isinstance(events, list):
            return original_content
        for idx, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            event_id = event.get("event_id")
            if event_id in ("dialogic_001", "text") and "text" in event:
                event["text"] = translations.get(f"{source_file}::E{idx}::text", event["text"])
            if event_id in ("dialogic_010", "choice") and "choice" in event:
                event["choice"] = translations.get(f"{source_file}::E{idx}::choice", event["choice"])
        return json.dumps(data, indent=4, ensure_ascii=False)

    @classmethod
    def parse_d1_json(
        cls, json_content: str, source_file: str = "timeline.json"
    ) -> list[HarvestedEntry]:
        """Extracts text from Dialogic 1.x timeline JSON arrays."""
        entries: list[HarvestedEntry] = []
        try:
            data = json.loads(json_content)
        except json.JSONDecodeError:
            return entries

        events = data.get("events", []) if isinstance(data, dict) else data
        if not isinstance(events, list):
            return entries

        for idx, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            cls._harvest_d1_event(event, idx, source_file, entries)

        return entries

    @classmethod
    def _harvest_d1_event(
        cls, event: dict[str, Any], idx: int, source_file: str, out: list[HarvestedEntry]
    ) -> None:
        """Parses a single Dialogic 1.x event dictionary."""
        # Event id 'dialogic_001' represents text
        event_id = event.get("event_id")
        if event_id in ("dialogic_001", "text") and "text" in event:
            text_val = str(event["text"]).strip()
            if text_val:
                speaker = str(event.get("character", "Narrator"))
                key = f"{source_file}::E{idx}::text"
                out.append(
                    HarvestedEntry(
                        key=key,
                        source_text=text_val,
                        source_file=source_file,
                        source_type=StringSourceType.NARRATIVE,
                        context=speaker,
                        line_number=idx,
                        metadata={"framework": "dialogic1", "event_index": idx},
                    )
                )

        # Event id 'dialogic_010' represents choice
        if event_id in ("dialogic_010", "choice") and "choice" in event:
            choice_val = str(event["choice"]).strip()
            if choice_val:
                key = f"{source_file}::E{idx}::choice"
                out.append(
                    HarvestedEntry(
                        key=key,
                        source_text=choice_val,
                        source_file=source_file,
                        source_type=StringSourceType.NARRATIVE,
                        context="Choice",
                        line_number=idx,
                        metadata={"framework": "dialogic1", "event_index": idx},
                    )
                )

    @classmethod
    def parse_generic_json(
        cls, json_content: str, source_file: str = "data.json", extra_fields: Iterable[str] = ()
    ) -> list[HarvestedEntry]:
        """Recursively harvests narrative text fields from custom JSON data tables.

        ``extra_fields`` adds game-specific key names (for example ``card_description``) to the built-in list.
        """
        fields = frozenset(cls.GENERIC_JSON_FIELDS) | frozenset(extra_fields)
        entries: list[HarvestedEntry] = []
        try:
            data = json.loads(json_content.lstrip("\ufeff"))
        except json.JSONDecodeError:
            return entries

        for _container, _slot, path, text in cls._json_text_slots(data, "", fields):
            entries.append(
                HarvestedEntry(
                    key=f"{source_file}::{path}",
                    source_text=text.strip(),
                    source_file=source_file,
                    source_type=StringSourceType.NARRATIVE,
                    context=path,
                    line_number=len(entries) + 1,
                    metadata={"json_path": path},
                )
            )
        return entries

    @classmethod
    def _json_text_slots(
        cls, node: Any, path: str, fields: frozenset[str], in_field: bool = False
    ) -> Iterator[tuple[Any, Any, str, str]]:
        """Yields (container, key or index, json path, text) for every translatable string.

        A string is translatable when its key is a text field, or when it is an item of a list stored
        under such a field (``"lines": ["Hello there", ...]``).
        """
        if isinstance(node, dict):
            children: Iterable[tuple[Any, Any, bool]] = ((k, v, k in fields) for k, v in node.items())
            join = lambda k: f"{path}.{k}" if path else str(k)  # noqa: E731
        elif isinstance(node, list):
            children = ((i, v, in_field) for i, v in enumerate(node))
            join = lambda i: f"{path}[{i}]"  # noqa: E731
        else:
            return
        for slot, value, is_text in children:
            child_path = join(slot)
            if isinstance(value, str):
                if is_text and value.strip():
                    yield node, slot, child_path, value
            else:
                yield from cls._json_text_slots(value, child_path, fields, is_text)

    @classmethod
    def apply_generic_json_translations(
        cls,
        original_content: str,
        translations: dict[str, str],
        source_file: str,
        extra_fields: Iterable[str] = (),
    ) -> str:
        """Applies translated strings back into a generic narrative JSON file."""
        fields = frozenset(cls.GENERIC_JSON_FIELDS) | frozenset(extra_fields)
        try:
            data = json.loads(original_content.lstrip("\ufeff"))
        except json.JSONDecodeError:
            return original_content

        for container, slot, path, _text in list(cls._json_text_slots(data, "", fields)):
            translated = translations.get(f"{source_file}::{path}")
            if translated:
                container[slot] = translated
        return json.dumps(data, indent=4, ensure_ascii=False)
