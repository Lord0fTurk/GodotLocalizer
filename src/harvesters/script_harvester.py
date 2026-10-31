"""Harvester for hardcoded string literals inside GDScript source files (.gd)."""

import re
from typing import ClassVar

from src.harvesters.base import HarvestedEntry, StringSourceType


class ScriptHarvester:
    """Scans GDScript source code for translatable hardcoded strings.

    Strings only reach the player through ``tr()`` or auto-translating controls, and the pipeline
    ships them in the runtime translation table (scripts are never rewritten), so the goal is to
    pick human-readable text and leave identifiers, paths, signals and node names alone.
    """

    # One string literal, single or double quoted, with backslash escapes.
    STRING_LITERAL_PATTERN = re.compile(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'')

    # Lines whose literals are identifiers or paths, never player-facing text.
    NON_TEXT_LINE_PATTERN = re.compile(
        r"^\s*(?:extends|class_name|const\s+\w+\s*=\s*(?:preload|load)|@\w+|signal|match|when)\b"
        r"|\b(?:print|prints|printt|printraw|printerr|print_rich|print_debug|push_error|push_warning)\s*\("
        r"|\b(?:_?log\w*|\w+_log|_?debug\w*)\s*\("
        r"|\b(?:preload|load|get_node(?:_or_null)?|has_node|find_child|connect|disconnect|emit_signal|"
        r"add_to_group|is_in_group|get_tree\(\)\.call_group|call_deferred|has_method|has_signal|"
        r"is_action_pressed|is_action_just_pressed|is_action_released|get_action_strength|"
        r"get_meta|set_meta|has_meta|assert)\s*\(",
    )

    # "INFO: loaded chat" and friends are developer log lines even when built outside a print() call.
    LOG_PREFIX_PATTERN = re.compile(r"^(?:INFO|ERROR|WARN(?:ING)?|DEBUG|TRACE|FATAL)\b\s*[:\]-]")

    IGNORED_EXACT_STRINGS: ClassVar[set[str]] = {
        "",
        "res://",
        "user://",
        "ready",
        "process",
        "physics_process",
        "timeout",
        "pressed",
    }

    @classmethod
    def harvest_gd(cls, script_content: str, source_file: str = "script.gd") -> list[HarvestedEntry]:
        """Extracts candidate translatable string literals from GDScript source code."""
        entries: list[HarvestedEntry] = []
        for line_num, line in enumerate(script_content.splitlines(), start=1):
            code_part = cls._strip_comment(line)
            if not code_part.strip() or cls.NON_TEXT_LINE_PATTERN.search(code_part):
                continue

            for match in cls.STRING_LITERAL_PATTERN.finditer(code_part):
                raw_text = match.group(1) if match.group(1) is not None else match.group(2)
                cleaned = cls._unescape(raw_text)
                if cls._is_translatable_candidate(cleaned):
                    entries.append(
                        HarvestedEntry(
                            key=f"{source_file}::L{line_num}::{cleaned[:24]}",
                            source_text=cleaned,
                            source_file=source_file,
                            source_type=StringSourceType.SCRIPT_CODE,
                            context=f"Line {line_num}",
                            line_number=line_num,
                            metadata={"language": "gdscript"},
                        )
                    )
        return entries

    @staticmethod
    def _unescape(text: str) -> str:
        return text.replace("\\\\", "\x00").replace('\\"', '"').replace("\\'", "'").replace("\\n", "\n").replace(
            "\\t", "\t"
        ).replace("\x00", "\\")

    @staticmethod
    def _strip_comment(line: str) -> str:
        """Cuts the line at the first ``#`` that is not inside a string literal."""
        quote = ""
        escaped = False
        for index, char in enumerate(line):
            if escaped:
                escaped = False
            elif char == "\\" and quote:
                escaped = True
            elif quote:
                if char == quote:
                    quote = ""
            elif char in "\"'":
                quote = char
            elif char == "#":
                return line[:index]
        return line

    FRAGMENT_PATTERN = re.compile(r"^\s+[(;,=:\[]|[=:(\[,;]\s*$|=>|\bconst \w+\s*=|\bfunction\b|^\s*\n\s*\t|^\*\.\w+$")

    @classmethod
    def is_player_text(cls, text: str) -> bool:
        """Candidate check for strings seen without any code context (compiled-script constants).

        Besides the usual shape rules this drops concatenation fragments such as ``" (seen="`` or
        ``"Build Base Directory: "`` that only make sense next to the variable they are glued to.
        """
        return cls._is_translatable_candidate(text) and not cls.FRAGMENT_PATTERN.search(text)

    @classmethod
    def _is_translatable_candidate(cls, text: str) -> bool:
        """Determines if a string literal appears to be human dialogue/UI text."""
        trimmed = text.strip()
        if len(trimmed) < 2 or trimmed in cls.IGNORED_EXACT_STRINGS:
            return False
        if trimmed.startswith(("res://", "user://", "uid://", "%", "$", "/root")):
            return False
        if cls.LOG_PREFIX_PATTERN.match(trimmed):
            return False
        if " " in trimmed and trimmed[0].islower() and trimmed == trimmed.lower() and not trimmed.endswith(("!", "?")):
            return False  # lowercase prose without any capital is a developer log message, not UI text
        if re.fullmatch(r"[A-Za-z0-9_./:%\-]+", trimmed) and " " not in trimmed:
            # Single identifier-like token: only keep capitalised words ("Continue", "Quit") as UI labels.
            return trimmed[0].isupper() and trimmed[1:].islower() and trimmed.isalpha()
        return any(ch.isalpha() for ch in trimmed)
