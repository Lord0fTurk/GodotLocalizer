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

        # Find key column and source language column index. Godot 4.6 adds ?context / ?plural columns and
        # ``_comment`` columns that are not languages.
        clean_header = [col.strip().lower() for col in header]
        key_idx = 0
        context_idx = clean_header.index("?context") if "?context" in clean_header else -1
        plural_idx = clean_header.index("?plural") if "?plural" in clean_header else -1
        special = {key_idx, context_idx, plural_idx} | {i for i, c in enumerate(clean_header) if c.startswith("_")}
        src_idx = next((i for i in range(1, len(clean_header)) if i not in special), 1)
        if source_lang_col.lower() in clean_header:
            src_idx = clean_header.index(source_lang_col.lower())
        plain_text_keys = context_idx >= 0 or plural_idx >= 0  # these files use the text itself as the key

        def cell(row: list[str], index: int) -> str:
            return row[index].strip() if 0 <= index < len(row) else ""

        for line_num, row in enumerate(reader, start=2):
            key_id = cell(row, key_idx)
            if not key_id or key_id.lower() == "?pluralrule":
                continue  # blank key = continuation row of a plural block; ?pluralrule = a rule, not a message

            src_text = cell(row, src_idx) or (key_id if plain_text_keys else "")
            context, plural_text = cell(row, context_idx), cell(row, plural_idx)
            base = f"{source_file}::{context}\x04{key_id}" if context else f"{source_file}::{key_id}"
            metadata: dict[str, object] = {"translation_key": key_id, "header": clean_header}
            if context:
                metadata["msgctxt"] = context
            forms = [(base, src_text, {})]
            if plural_text:
                metadata["plural_group"] = base
                forms = [(base, src_text, {"plural_form": 0}), (f"{base}#plural", plural_text, {"plural_form": 1})]
            entries.extend(
                HarvestedEntry(
                    key=key,
                    source_text=text,
                    source_file=source_file,
                    source_type=StringSourceType.TRANSLATION,
                    context=f"CSV ID: {key_id}",
                    line_number=line_num,
                    metadata={**metadata, **extra},
                )
                for key, text, extra in forms
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
        plural = block.get("msgid_plural", "")
        key = f"{source_file}::L{line_num}::{label[:32]}"
        if plural:
            metadata["msgid_plural"] = plural
            metadata["plural_group"] = key
        out.append(
            HarvestedEntry(
                key=key,
                source_text=msgid,
                source_file=source_file,
                source_type=StringSourceType.TRANSLATION,
                context=f"Gettext msgid ({ctx})" if ctx else "Gettext msgid",
                line_number=line_num,
                translation=block.get("msgstr", block.get("msgstr[0]", "")),
                metadata={**metadata, **({"plural_form": 0} if plural else {})},
            )
        )
        if plural:  # the plural form is its own string to translate; the pair is rebuilt as one tr_n() message
            out.append(
                HarvestedEntry(
                    key=f"{key}#plural",
                    source_text=plural,
                    source_file=source_file,
                    source_type=StringSourceType.TRANSLATION,
                    context=f"Gettext msgid_plural ({ctx})" if ctx else "Gettext msgid_plural",
                    line_number=line_num,
                    metadata={**metadata, "plural_form": 1},
                )
            )
