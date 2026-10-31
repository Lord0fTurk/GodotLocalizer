"""Harvester for extracting UI and dialog text from Godot scene files (.tscn / .scn)."""

import re
from collections.abc import Iterator
from typing import ClassVar

from src.harvesters.base import HarvestedEntry, StringSourceType


def _quotes_balanced(text: str) -> bool:
    """True when every double quote in ``text`` is closed (backslash-escaped quotes are ignored)."""
    return len(re.findall(r'(?<!\\)"', text)) % 2 == 0


class SceneHarvester:
    """Extracts UI strings from Godot PackedScene definitions."""

    TRANSLATABLE_PROPERTIES: ClassVar[set[str]] = {
        "text",
        "bbcode_text",
        "placeholder_text",
        "dialog_text",
        "title",
        "tooltip_text",
    }

    # OptionButton / PopupMenu entries and tab titles are stored as indexed properties.
    INDEXED_TEXT_PATTERN = re.compile(r"^(?:popup/item_\d+/text|item_\d+/text|tab_\d+/title)$")

    # Matches Godot node headers: [node name="PlayBtn" type="Button" parent="VBox"]
    NODE_HEADER_PATTERN = re.compile(
        r'^\[node\s+name="(?P<name>[^"]+)"(?:\s+type="(?P<type>[^"]+)")?(?:\s+parent="(?P<parent>[^"]*)")?'
    )

    # Matches property assignments: text = "Press Start" (the value may span several physical lines)
    PROP_PATTERN = re.compile(
        r'^\s*(?P<prop>[a-zA-Z0-9_/]+)\s*=\s*"(?P<val>.*)"\s*$', re.DOTALL
    )

    @classmethod
    def is_text_property(cls, name: str) -> bool:
        """True for node properties whose string value is shown to the player."""
        return name in cls.TRANSLATABLE_PROPERTIES or bool(cls.INDEXED_TEXT_PATTERN.match(name))

    @staticmethod
    def _logical_lines(content: str) -> Iterator[tuple[int, str]]:
        """Yields (first line number, text with line endings) merging strings that span lines."""
        pending, start = "", 0
        for line_num, line in enumerate(content.splitlines(keepends=True), start=1):
            if not pending:
                start = line_num
            pending += line
            if _quotes_balanced(pending):
                yield start, pending
                pending = ""
        if pending:
            yield start, pending

    @classmethod
    def harvest_tscn(cls, content: str, source_file: str = "scene.tscn") -> list[HarvestedEntry]:
        """Harvests translatable properties across node definitions in a .tscn file."""
        entries: list[HarvestedEntry] = []
        current_node_path = "Root"
        current_node_type = "Node"

        for line_num, raw_line in cls._logical_lines(content):
            stripped = raw_line.strip()

            # Check if entering a new node section
            node_match = cls.NODE_HEADER_PATTERN.match(stripped)
            if node_match:
                name = node_match.group("name")
                parent = node_match.group("parent")
                current_node_type = node_match.group("type") or "Node"
                current_node_path = f"{parent}/{name}" if parent else name
                continue

            # Check if line defines a property
            prop_match = cls.PROP_PATTERN.match(stripped)
            if prop_match and cls.is_text_property(prop_match.group("prop")):
                entry = cls._create_entry(
                    prop_match.group("val"),
                    prop_match.group("prop"),
                    current_node_path,
                    current_node_type,
                    line_num,
                    source_file,
                )
                if entry:
                    entries.append(entry)

        return entries

    @classmethod
    def apply_tscn_translations(cls, content: str, translations: dict[str, str], source_file: str) -> str:
        """Writes translated values back into a .tscn, keeping line endings and untouched lines intact."""
        out: list[str] = []
        node_path = "Root"
        for _line_num, raw_line in cls._logical_lines(content):
            body = raw_line.rstrip("\r\n")
            eol = raw_line[len(body):]
            stripped = body.strip()

            node_match = cls.NODE_HEADER_PATTERN.match(stripped)
            if node_match:
                parent = node_match.group("parent")
                name = node_match.group("name")
                node_path = f"{parent}/{name}" if parent else name
                out.append(raw_line)
                continue

            prop_match = cls.PROP_PATTERN.match(stripped)
            if prop_match and cls.is_text_property(prop_match.group("prop")):
                prop = prop_match.group("prop")
                translated = translations.get(f"{source_file}::{node_path}::{prop}")
                if translated:
                    indent = body[: len(body) - len(body.lstrip())]
                    escaped = (
                        translated.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
                    )
                    out.append(f'{indent}{prop} = "{escaped}"{eol}')
                    continue
            out.append(raw_line)
        return "".join(out)

    @classmethod
    def _create_entry(
        cls,
        raw_val: str,
        prop_name: str,
        node_path: str,
        node_type: str,
        line_num: int,
        source_file: str,
    ) -> HarvestedEntry | None:
        """Builds a HarvestedEntry with unescaped characters."""
        # Unescape standard Godot escape sequences
        cleaned = raw_val.replace("\\\\", "\x00").replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")
        cleaned = cleaned.replace("\x00", "\\")
        if not cleaned.strip():
            return None

        key = f"{source_file}::{node_path}::{prop_name}"
        return HarvestedEntry(
            key=key,
            source_text=cleaned,
            source_file=source_file,
            source_type=StringSourceType.SCENE_UI,
            context=f"{node_type} ({node_path})",
            line_number=line_num,
            metadata={"node_type": node_type, "property": prop_name, "node_path": node_path},
        )
