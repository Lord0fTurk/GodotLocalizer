"""Harvester for official Godot translation tables (.csv, .po, and .translation)."""

import csv
import io
import re

from src.harvesters.base import HarvestedEntry, StringSourceType

_PO_FIELD_RE = re.compile(r"^(msgctxt|msgid_plural|msgid|msgstr(?:\[\d+\])?)\s+(.*)$")


class TranslationHarvester:
    """Parses standard CSV translation catalogs, Gettext PO files, and binary translation resources."""

    @classmethod
    def harvest_csv(
        cls,
        csv_content: str,
        source_file: str = "translations.csv",
        source_lang_col: str = "en",
    ) -> list[HarvestedEntry]:
        """Harvests translatable keys and values from Godot localization CSV files."""
        entries: list[HarvestedEntry] = []
        reader = csv.reader(io.StringIO(csv_content))

        try:
            header = next(reader)
        except StopIteration:
            return entries

        # Find key column and source language column index
        clean_header = [col.strip().lower() for col in header]
        key_idx = 0
        src_idx = 1
        if source_lang_col.lower() in clean_header:
            src_idx = clean_header.index(source_lang_col.lower())

        for line_num, row in enumerate(reader, start=2):
            if not row or len(row) <= key_idx:
                continue
            key_id = row[key_idx].strip()
            if not key_id:
                continue

            src_text = row[src_idx].strip() if len(row) > src_idx else key_id
            entries.append(
                HarvestedEntry(
                    key=f"{source_file}::{key_id}",
                    source_text=src_text,
                    source_file=source_file,
                    source_type=StringSourceType.TRANSLATION,
                    context=f"CSV ID: {key_id}",
                    line_number=line_num,
                    metadata={"translation_key": key_id, "header": clean_header},
                )
            )

        return entries

    @classmethod
    def harvest_po(cls, po_content: str, source_file: str = "messages.po") -> list[HarvestedEntry]:
        """Extracts messages from Gettext PO files, honouring msgctxt, plural forms and fuzzy flags."""
        entries: list[HarvestedEntry] = []
        block: dict[str, str] = {}
        field = ""
        start_line = 1
        fuzzy = False

        def flush() -> None:
            cls._append_po_entry(block, start_line, source_file, fuzzy, entries)

        for line_num, line in enumerate(po_content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                if block:
                    flush()
                block, field, fuzzy = {}, "", False
                continue
            if stripped.startswith("#"):
                if block:  # a comment after fields starts the next entry
                    flush()
                    block, field, fuzzy = {}, "", False
                fuzzy = fuzzy or (stripped.startswith("#,") and "fuzzy" in stripped)
                continue

            match = _PO_FIELD_RE.match(stripped)
            if match:
                name, value = match.groups()
                if name in ("msgctxt", "msgid") and (name in block or "msgstr" in block):
                    flush()
                    block, fuzzy = {}, False
                if not block:
                    start_line = line_num
                field = name
                block[field] = cls._unquote_po_line(value)
            elif stripped.startswith('"') and field:
                block[field] += cls._unquote_po_line(stripped)

        if block:
            flush()
        return entries

    @classmethod
    def _unquote_po_line(cls, quoted_str: str) -> str:
        """Removes enclosing quotes and unescapes standard Gettext sequences."""
        text = quoted_str.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        return text.replace("\\\\", "\x00").replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t").replace(
            "\x00", "\\"
        )

    @classmethod
    def _append_po_entry(
        cls, block: dict[str, str], line_num: int, source_file: str, fuzzy: bool, out: list[HarvestedEntry]
    ) -> None:
        """Appends a valid PO message to the results list (the header msgid "" is skipped)."""
        msgid = block.get("msgid", "")
        if not msgid:
            return
        ctx = block.get("msgctxt", "")
        label = f"{ctx}\x04{msgid}" if ctx else msgid
        metadata: dict[str, object] = {"format": "po", "translation_key": msgid, "fuzzy": fuzzy}
        if ctx:
            metadata["msgctxt"] = ctx
        if "msgid_plural" in block:
            metadata["msgid_plural"] = block["msgid_plural"]
        out.append(
            HarvestedEntry(
                key=f"{source_file}::L{line_num}::{label[:32]}",
                source_text=msgid,
                source_file=source_file,
                source_type=StringSourceType.TRANSLATION,
                context=f"Gettext msgid ({ctx})" if ctx else "Gettext msgid",
                line_number=line_num,
                translation=block.get("msgstr", block.get("msgstr[0]", "")),
                metadata=metadata,
            )
        )
