"""Message contexts, plural forms and compiled .mo catalogs: harvested, kept apart and shipped to the runtime table."""

import struct
import tempfile
import unittest
from pathlib import Path

from src.core.binary.mo import MoError, parse_mo
from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter
from src.core.pipeline.harvest import (
    KIND_MO,
    build_runtime_extras,
    build_runtime_messages,
    classify,
    harvest_pack,
)
from src.harvesters.binary_harvester import parse_translation_resource
from src.harvesters.translation_harvester import TranslationHarvester
from src.patcher.bootstrap_script import BootstrapScriptGenerator, build_translations_payload
from tests.rsrc_builder import build_rsrc, v_array, v_dict, v_str


def build_mo(messages: list[tuple[bytes, bytes]]) -> bytes:
    """Little-endian GNU .mo catalog; originals and translations are sorted as gettext requires."""
    messages = sorted(messages)
    count, header = len(messages), 28
    originals_at, translations_at = header, header + count * 8
    blob_at = translations_at + count * 8
    blob, o_table, t_table = b"", b"", b""
    for original, _translated in messages:
        o_table += struct.pack("<II", len(original), blob_at + len(blob))
        blob += original + b"\x00"
    for _original, translated in messages:
        t_table += struct.pack("<II", len(translated), blob_at + len(blob))
        blob += translated + b"\x00"
    return struct.pack("<IIIIIII", 0x950412DE, 0, count, originals_at, translations_at, 0, 0) + o_table + t_table + blob


HEADER = (b"", b"Language: en\nPlural-Forms: nplurals=2; plural=(n != 1);\n")


class TestCsvContextAndPlural(unittest.TestCase):
    CSV = (
        "keys,?context,?plural,_note,en,tr\n"
        "Letter,Alphabet,,x,A letter,\n"
        "Letter,Message,,x,Mail,\n"
        "%d apple,,%d apples,x,%d apple,\n"
        ",,,,%d apples,\n"
        "?pluralrule,,,,nplurals=2; plural=(n != 1);,nplurals=1; plural=0;\n"
    )

    def test_contexts_plurals_and_rules(self) -> None:
        entries = TranslationHarvester.harvest_csv(self.CSV, "res://t.csv")
        self.assertEqual(
            [(e.source_text, e.metadata.get("msgctxt", ""), e.metadata.get("plural_form")) for e in entries],
            [("A letter", "Alphabet", None), ("Mail", "Message", None), ("%d apple", "", 0), ("%d apples", "", 1)],
        )
        self.assertEqual(len({e.key for e in entries}), 4)  # the same key in two contexts stays two entries
        self.assertEqual(entries[2].metadata["plural_group"], entries[3].metadata["plural_group"])

    def test_plain_csv_is_unchanged(self) -> None:
        entries = TranslationHarvester.harvest_csv("keys,en,tr\nUI_START,Start,\n", "res://t.csv")
        self.assertEqual([(e.key, e.source_text) for e in entries], [("res://t.csv::UI_START", "Start")])


class TestBinaryContextAndPlural(unittest.TestCase):
    DATA = build_rsrc("Translation", [
        ("locale", v_str("en")),
        ("messages", v_dict([
            (v_array([v_str("Main Menu"), v_str("Shop")]), v_array([v_str("Shop")])),
            (v_array([v_str(""), v_str("APPLES")]), v_array([v_str("%d apple"), v_str("%d apples")])),
        ])),
    ])

    def test_context_and_plural_forms_are_read(self) -> None:
        _locale, entries, _raw = parse_translation_resource(self.DATA, "res://en.translation")  # type: ignore[misc]
        shop, singular, plural = entries
        self.assertEqual((shop.metadata["msgctxt"], shop.metadata["translation_key"]), ("Main Menu", "Shop"))
        self.assertEqual((singular.source_text, plural.source_text), ("%d apple", "%d apples"))
        self.assertEqual(singular.metadata["plural_group"], plural.metadata["plural_group"])

    def test_runtime_rows(self) -> None:
        _locale, entries, _raw = parse_translation_resource(self.DATA, "res://en.translation")  # type: ignore[misc]
        for entry in entries:
            entry.translation = f"T:{entry.source_text}"
        extras = build_runtime_extras(entries)
        self.assertEqual(extras["contextual"], [["Main Menu", "Shop", "T:Shop"]])
        context, msgid, forms = extras["plurals"][0]
        self.assertEqual((context, msgid, forms[:2], len(forms)), ("", "APPLES", ["T:%d apple", "T:%d apples"], 6))
        # the plural form must not overwrite the singular's flat entry
        self.assertEqual(build_runtime_messages(entries)["APPLES"], "T:%d apple")

    def test_payload_and_bootstrap_carry_them(self) -> None:
        payload = build_translations_payload("tr", {}, None, {"contextual": [["c", "Shop", "Magaza"]]})
        self.assertIn('"contextual": [["c", "Shop", "Magaza"]]', payload.decode())
        self.assertIn("add_plural_message", BootstrapScriptGenerator.generate_godot4_bootstrap())
        self.assertNotIn("add_plural_message", BootstrapScriptGenerator.generate_godot3_bootstrap())


class TestMoCatalogs(unittest.TestCase):
    MO = build_mo([
        HEADER,
        (b"UI_START", b"Start the game"),
        (b"Door\x04Open", b"Door open"),
        (b"%d coin\x00%d coins", b"%d coin\x00%d coins"),
    ])

    def test_parse(self) -> None:
        catalog = parse_mo(self.MO)
        self.assertEqual((catalog.locale, "nplurals=2" in catalog.plural_forms), ("en", True))
        by_id = {m.msgid: m for m in catalog.messages}
        self.assertEqual(by_id["Open"].context, "Door")
        self.assertEqual(by_id["%d coin"].msgid_plural, "%d coins")
        with self.assertRaises(MoError):
            parse_mo(b"not a catalog at all, nope nope")

    def test_harvest_uses_text_not_keys_and_never_rewrites_the_file(self) -> None:
        self.assertEqual(classify("res://i18n/en.mo"), KIND_MO)
        with tempfile.TemporaryDirectory() as tmp:
            writer = PCKWriter(Path(tmp) / "g.pck", engine_version=(4, 2, 0))
            writer.add_bytes("res://i18n/en.mo", self.MO)
            writer.build()
            reader = PCKReader(Path(tmp) / "g.pck")
            reader.open()
            texts = sorted(e.source_text for e in harvest_pack(reader))
        self.assertEqual(texts, ["%d coin", "%d coins", "Open", "Start the game"])


if __name__ == "__main__":
    unittest.main()
