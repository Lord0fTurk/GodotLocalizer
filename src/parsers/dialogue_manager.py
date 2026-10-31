"""Parser and text replacer for Nathan Hoad's Dialogue Manager format (.dialogue)."""

import re

from src.harvesters.base import HarvestedEntry, StringSourceType


class DialogueManagerParser:
    """Extracts translatable spoken lines and choices from .dialogue files and enables reconstruction."""

    # Matches lines like: Nathan: Hello there! or Nathan (happy): Welcome!
    LINE_PATTERN = re.compile(
        r"^(?P<indent>\t*|\s*)(?P<speaker>[A-Za-z0-9_]+)?\s*(?:\([^)]*\))?:\s*"
        r"(?P<text>[^#\n\r]+?)(?P<tag>[ \t]*\[ID:[^\]]*\])?(?P<suffix>\s*(?:#.*)?)$"
    )
    # Logic lines that contain colons or look like text but are never spoken dialogue.
    LOGIC_PATTERN = re.compile(r"^\s*(?:if|elif|else|while|match|when|do!?|set|using|import)\b|^\s*=>")
    # Matches choice prompts: - Open the magic chest => open_chest
    CHOICE_PATTERN = re.compile(
        r"^(?P<indent>\t*|\s*)-\s*(?P<prompt>[^#=><\n\r\[]+)(?P<suffix>.*)$"
    )
    # Matches compiled DialogueResource (.tres) line text fields: &"text": "Hello!" or "text": "Hello!"
    TRES_TEXT_PATTERN = re.compile(
        r'^(?P<prefix>\s*&?"text"\s*:\s*)"(?P<val>(?:[^"\\]|\\.)*)"(?P<suffix>\s*,?\s*)$',
        re.MULTILINE,
    )

    @classmethod
    def parse_content(
        cls, content: str, source_file: str = "dialogue.dialogue"
    ) -> list[HarvestedEntry]:
        """Harvests translatable text elements from raw .dialogue file content."""
        entries: list[HarvestedEntry] = []
        lines = content.splitlines()

        for line_num, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("~", "#")) or cls.LOGIC_PATTERN.match(line):
                continue

            entry = cls._parse_single_line(line, line_num, source_file)
            if entry:
                entries.append(entry)

        return entries

    @classmethod
    def _parse_single_line(
        cls, line: str, line_num: int, source_file: str
    ) -> HarvestedEntry | None:
        """Tries parsing a line as a spoken dialogue or choice option."""
        # 1. Try spoken line
        line_match = cls.LINE_PATTERN.match(line)
        if line_match:
            speaker = line_match.group("speaker") or "Narrator"
            text_val = line_match.group("text").strip()
            if text_val:
                key = f"{source_file}::L{line_num}::{speaker}"
                return HarvestedEntry(
                    key=key,
                    source_text=text_val,
                    source_file=source_file,
                    source_type=StringSourceType.NARRATIVE,
                    context=speaker,
                    line_number=line_num,
                    metadata={"kind": "dialogue_line", "speaker": speaker},
                )

        # 2. Try choice option
        choice_match = cls.CHOICE_PATTERN.match(line)
        if choice_match:
            choice_val = choice_match.group("prompt").strip()
            if choice_val:
                key = f"{source_file}::L{line_num}::choice"
                return HarvestedEntry(
                    key=key,
                    source_text=choice_val,
                    source_file=source_file,
                    source_type=StringSourceType.NARRATIVE,
                    context="Choice",
                    line_number=line_num,
                    metadata={"kind": "choice_option"},
                )

        return None

    @classmethod
    def apply_translations(
        cls, original_content: str, translations: dict[str, str], source_file: str
    ) -> str:
        """Replaces source text lines with translations while preserving structure."""
        eol = "\r\n" if "\r\n" in original_content else "\n"
        lines = original_content.splitlines()
        updated_lines: list[str] = []

        for line_num, line in enumerate(lines, start=1):
            updated_line = cls._replace_line_if_translated(
                line, line_num, translations, source_file
            )
            updated_lines.append(updated_line)

        trailing = eol if original_content.endswith(("\n", "\r")) else ""
        return eol.join(updated_lines) + trailing

    @classmethod
    def _replace_line_if_translated(
        cls, line: str, line_num: int, translations: dict[str, str], source_file: str
    ) -> str:
        """Substitutes dialogue text or choice prompt in a single line."""
        if cls.LOGIC_PATTERN.match(line):
            return line

        line_match = cls.LINE_PATTERN.match(line)
        if line_match:
            speaker = line_match.group("speaker") or "Narrator"
            translated = translations.get(f"{source_file}::L{line_num}::{speaker}")
            if translated:
                # Splice into the original line so "(mood)" modifiers, [ID:] tags and comments survive.
                return line[: line_match.start("text")] + translated + line[line_match.end("text"):]

        choice_match = cls.CHOICE_PATTERN.match(line)
        if choice_match:
            translated = translations.get(f"{source_file}::L{line_num}::choice")
            if translated:
                prompt = choice_match.group("prompt")
                trailing = prompt[len(prompt.rstrip()):]
                return line[: choice_match.start("prompt")] + translated + trailing + choice_match.group("suffix")

        return line

    @classmethod
    def parse_compiled_tres(
        cls, content: str, source_file: str = "dialogue.tres"
    ) -> list[HarvestedEntry]:
        """Harvests spoken dialogue and choice prompts from compiled DialogueResource .tres files."""
        entries: list[HarvestedEntry] = []
        raw_text_idx = content.find("raw_text =")
        search_region = content[:raw_text_idx] if raw_text_idx != -1 else content

        for idx, match in enumerate(cls.TRES_TEXT_PATTERN.finditer(search_region), start=1):
            raw_val = match.group("val")
            cleaned = raw_val.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")
            if not cleaned.strip():
                continue
            key = f"{source_file}::TRES_{idx}"
            entries.append(
                HarvestedEntry(
                    key=key,
                    source_text=cleaned,
                    source_file=source_file,
                    source_type=StringSourceType.NARRATIVE,
                    context="DialogueResource",
                    line_number=idx,
                    metadata={"kind": "compiled_tres", "index": idx},
                )
            )
        return entries

    @classmethod
    def apply_compiled_tres_translations(
        cls, original_content: str, translations: dict[str, str], source_file: str
    ) -> str:
        """Replaces `&\"text\": \"...\"` entries inside compiled DialogueResource .tres files."""
        raw_text_idx = original_content.find("raw_text =")
        head = original_content[:raw_text_idx] if raw_text_idx != -1 else original_content
        tail = original_content[raw_text_idx:] if raw_text_idx != -1 else ""

        counter = 0

        def _repl(match: re.Match[str]) -> str:
            nonlocal counter
            raw_val = match.group("val")
            cleaned = raw_val.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")
            if not cleaned.strip():
                return match.group(0)
            counter += 1
            key = f"{source_file}::TRES_{counter}"
            translated = translations.get(key)
            if not translated:
                return match.group(0)
            escaped = translated.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
            return f'{match.group("prefix")}"{escaped}"{match.group("suffix")}'

        updated_head = cls.TRES_TEXT_PATTERN.sub(_repl, head)
        return updated_head + tail
