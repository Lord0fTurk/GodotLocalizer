"""Binary resource tests: RSRC reader, binary scene harvesting, smaz and OptimizedTranslation tables."""

import base64
import json
import tempfile
import unittest
from pathlib import Path

from src.core.binary import smaz
from src.core.binary.optimized_translation import read_elements, rebuild
from src.core.binary.rsrc import RSRCError, parse_rsrc
from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter
from src.core.pipeline.harvest import (
    build_optimized_translations,
    build_runtime_messages,
    harvest_pack,
    pick_source_locale,
)
from src.harvesters.binary_harvester import harvest_binary_resource, parse_translation_resource
from src.patcher.bootstrap_script import TRANSLATIONS_RES_PATH, BootstrapScriptGenerator
from src.patcher.overlay_patcher import OverlayPatcher
from tests.rsrc_builder import (
    build_rsrc,
    menu_scene,
    scene_bytes,
    v_array,
    v_bool,
    v_bytes,
    v_dict,
    v_float,
    v_ints,
    v_nil,
    v_str,
    v_strings,
    v_vec2,
)


def optimized_translation_bytes(locale: str = "en") -> tuple[bytes, list[int], list[int], bytes]:
    """Two values in two buckets: one stored raw, one smaz-compressed ([1,0,7] -> 'the and')."""
    strings = b"Start Game" + bytes([1, 0, 7])
    buckets = [
        1, 111, 0x11111111, 0, 10, 10,       # bucket @0: raw "Start Game"
        1, 222, 0x22222222, 10, 3, 7,        # bucket @6: smaz "the and"
    ]
    hash_table = [0, -1, 6, -1]
    data = build_rsrc("OptimizedTranslation", [
        ("locale", v_str(locale)),
        ("hash_table", v_ints(hash_table)),
        ("bucket_table", v_ints([b if b < 2**31 else b - 2**32 for b in buckets])),
        ("strings", v_bytes(strings)),
    ])
    return data, hash_table, buckets, strings


class TestRsrcReader(unittest.TestCase):
    def test_variants_and_internal_resources(self) -> None:
        sub = ("local://1", "StyleBoxFlat", [("corner", v_float(2.0))])
        data = build_rsrc("Resource", [
            ("name", v_str("héllo")),
            ("flag", v_bool(True)),
            ("nothing", v_nil()),
            ("pos", v_vec2(1.0, 2.0)),
            ("table", v_dict([(v_str("a"), v_array([v_str("x"), v_str("y")]))])),
            ("lines", v_strings(["one", "two"])),
        ], extra_resources=[sub])
        rsrc = parse_rsrc(data)
        self.assertEqual(rsrc.main.type, "Resource")
        props = rsrc.main.properties
        self.assertEqual(props["name"], "héllo")
        self.assertIs(props["flag"], True)
        self.assertIsNone(props["nothing"])
        self.assertEqual(props["pos"], (1.0, 2.0))
        self.assertEqual(props["table"], {"a": ["x", "y"]})
        self.assertEqual(props["lines"], ["one", "two"])
        self.assertEqual(rsrc.resources[0].path, "local://1")

    def test_godot3_header(self) -> None:
        rsrc = parse_rsrc(menu_scene(ver_major=3))
        self.assertEqual(rsrc.ver_major, 3)
        self.assertEqual(rsrc.main.type, "PackedScene")

    def test_rejects_garbage_and_compressed(self) -> None:
        for bad in (b"", b"GDPC" + b"\x00" * 40, b"RSCC" + b"\x00" * 40, menu_scene()[:60]):
            with self.assertRaises(RSRCError):
                parse_rsrc(bad)

    def test_unsupported_variant_only_breaks_its_resource(self) -> None:
        bad = ("local://1", "Broken", [("event", (25).to_bytes(4, "little"))])
        rsrc = parse_rsrc(build_rsrc("Resource", [("name", v_str("ok"))], extra_resources=[bad]))
        self.assertTrue(rsrc.resources[0].error)
        self.assertEqual(rsrc.main.properties["name"], "ok")

    def test_hostile_lengths_do_not_allocate(self) -> None:
        huge = (30).to_bytes(4, "little") + (0x7FFFFFFF).to_bytes(4, "little")
        rsrc = parse_rsrc(build_rsrc("Resource", [("x", huge)]))
        self.assertIn("exceeds", rsrc.main.error)


def rscc(stream: bytes, mode: int, block_size: int = 64) -> bytes:
    """Godot FileAccessCompressed container around a plain resource stream (written from the spec)."""
    import struct
    import zlib

    blocks = [stream[i:i + block_size] for i in range(0, len(stream), block_size)]
    if len(stream) % block_size == 0:
        blocks.append(b"")  # exact multiples end with an empty block
    if mode == 2:
        from compression import zstd

        packed = [zstd.compress(b) if b else b"" for b in blocks]
    else:
        packed = [zlib.compress(b) if b else b"" for b in blocks]
    header = b"RSCC" + struct.pack("<III", mode, block_size, len(stream))
    return header + b"".join(struct.pack("<I", len(p)) for p in packed) + b"".join(packed)


class TestCompressedResources(unittest.TestCase):
    def test_deflate_and_zstd_blocks_with_stream_relative_offsets(self) -> None:
        plain = menu_scene()
        # Offsets inside a compressed stream do not count the 4-byte magic.
        bundled_stream = build_rsrc("Resource", [("text", v_str("Compressed text"))], with_magic=False)
        for mode in (1, 2):
            try:
                data = rscc(bundled_stream, mode)
            except ImportError:
                continue
            parsed = parse_rsrc(data)
            self.assertEqual(parsed.main.properties["text"], "Compressed text")
        self.assertEqual(parse_rsrc(plain).main.type, "PackedScene")

    def test_unsupported_mode_is_a_clean_error(self) -> None:
        import struct

        with self.assertRaises(RSRCError):
            parse_rsrc(b"RSCC" + struct.pack("<III", 0, 4096, 10) + struct.pack("<II", 5, 0) + b"abcde")


class TestBinarySceneHarvest(unittest.TestCase):
    def test_text_properties_with_node_paths(self) -> None:
        for major in (4, 3):
            entries = harvest_binary_resource(menu_scene(major), "res://menu.scn")
            found = {(e.metadata["node_path"], e.metadata["property"]): e.source_text for e in entries}
            self.assertEqual(found, {
                ("Menu/Title", "text"): "Hello World",
                ("Menu/Title", "tooltip_text"): "The game title",
                ("Menu/Play", "text"): "Start Game",
            })
            self.assertEqual(entries[0].key, "res://menu.scn::Menu/Title::text")

    def test_inconsistent_node_table_falls_back_to_resource_walk(self) -> None:
        data = scene_bytes(["A"], [v_str("Hi")], [-1, -1, 0, 0, -1, 0, 0, 99], node_count=1)
        self.assertEqual(harvest_binary_resource(data, "res://x.scn"), [])

    def test_generic_resource_text_fields(self) -> None:
        data = build_rsrc("Resource", [
            ("dialogue", v_array([v_dict([(v_str("text"), v_str("Welcome, traveler")), (v_str("id"), v_str("npc_1"))])])),
        ])
        entries = harvest_binary_resource(data, "res://d.res")
        self.assertEqual([e.source_text for e in entries], ["Welcome, traveler"])


class TestGodot3Translations(unittest.TestCase):
    def test_phash_translation_is_recognised(self) -> None:
        _data, table, buckets, strings = optimized_translation_bytes()
        signed = [b if b < 2**31 else b - 2**32 for b in buckets]
        data = build_rsrc("PHashTranslation", [
            ("locale", v_str("en")),
            ("hash_table", v_ints(table)),
            ("bucket_table", v_ints(signed)),
            ("strings", v_bytes(strings)),
        ], ver_major=3)
        parsed = parse_translation_resource(data, "res://s.en.translation")
        self.assertIsNotNone(parsed)
        self.assertEqual([e.source_text for e in parsed[1]], ["Start Game", "the and"])
        self.assertIn("PHashTranslation.new()", BootstrapScriptGenerator.generate_godot3_bootstrap())
        self.assertNotIn("OptimizedTranslation", BootstrapScriptGenerator.generate_godot3_bootstrap())


class TestSmazAndOptimizedTranslation(unittest.TestCase):
    def test_codebook_shape(self) -> None:
        self.assertEqual(len(smaz.CODEBOOK), 254)
        self.assertEqual((smaz.CODEBOOK[0], smaz.CODEBOOK[1], smaz.CODEBOOK[253]), (b" ", b"the", b".com"))

    def test_decompress(self) -> None:
        self.assertEqual(smaz.decompress(bytes([1, 0, 7])), b"the and")
        self.assertEqual(smaz.decompress(bytes([254, 0x41, 255, 1, 0x42, 0x43])), b"ABC")
        with self.assertRaises(smaz.SmazError):
            smaz.decompress(bytes([255, 5, 1]))

    def test_read_and_rebuild(self) -> None:
        _data, table, buckets, strings = optimized_translation_bytes()
        signed = [b if b < 2**31 else b - 2**32 for b in buckets]
        elements = read_elements(table, signed, strings)
        self.assertEqual([e.text for e in elements], ["Start Game", "the and"])

        new_buckets, blob = rebuild(table, signed, strings, {"Start Game": "Oyunu Başlat"})
        texts = [e.text for e in read_elements(table, new_buckets, blob)]
        self.assertEqual(texts, ["Oyunu Başlat", "the and"])  # untouched values survive, hashes unchanged
        self.assertEqual(new_buckets[2], signed[2])
        self.assertEqual(new_buckets[8], signed[8])

    def test_missing_locale_property_means_english(self) -> None:
        """Real exports omit `locale` for English tables (it is the default and is not saved)."""
        _data, table, buckets, strings = optimized_translation_bytes()
        signed = [b if b < 2**31 else b - 2**32 for b in buckets]
        props = [("hash_table", v_ints(table)), ("bucket_table", v_ints(signed)), ("strings", v_bytes(strings))]
        english = parse_translation_resource(build_rsrc("OptimizedTranslation", props), "res://a.en.translation")
        self.assertEqual(english[0], "en")
        polish = parse_translation_resource(
            build_rsrc("OptimizedTranslation", [("locale", v_str("pl")), *props]), "res://a.pl.translation"
        )
        self.assertEqual(polish[0], "pl")
        # A larger non-English table must not win over the implicit English one.
        self.assertEqual(pick_source_locale({"en": 3, "pl": 9}), "en")

    def test_pick_source_locale(self) -> None:
        self.assertEqual(pick_source_locale({"de": 9, "en_US": 3}), "en_US")
        self.assertEqual(pick_source_locale({"de": 9, "fr": 3}), "de")
        self.assertEqual(pick_source_locale({"de": 9, "fr": 3}, "fr"), "fr")


class TestPackPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        en, *_ = optimized_translation_bytes("en")
        de, *_ = optimized_translation_bytes("de")
        plain = build_rsrc("Translation", [
            ("locale", v_str("en")),
            ("messages", v_dict([(v_array([v_str(""), v_str("MENU_EXIT")]), v_array([v_str("Exit Game")]))])),
        ])
        writer = PCKWriter(self.dir / "game.pck", engine_version=(4, 2, 0))
        writer.add_bytes("res://menu.scn", menu_scene())
        writer.add_bytes("res://ui.en.translation", en)
        writer.add_bytes("res://ui.de.translation", de)
        writer.add_bytes("res://plain.en.translation", plain)
        writer.add_bytes("res://tex.ctex", b"\x00" * 128)
        writer.build()
        self.reader = PCKReader(self.dir / "game.pck")
        self.reader.open()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_harvest_ignores_non_source_locales(self) -> None:
        entries = harvest_pack(self.reader)
        by_type = {e.source_text for e in entries}
        self.assertTrue({"Hello World", "Start Game", "the and", "Exit Game"} <= by_type)
        locales = {e.metadata["locale"] for e in entries if "locale" in e.metadata}
        self.assertEqual(locales, {"en"})

    def test_optimized_payload_reaches_the_patch(self) -> None:
        entries = harvest_pack(self.reader)
        for e in entries:
            e.translation = {"Start Game": "Oyunu Başlat", "Exit Game": "Çıkış"}.get(e.source_text, "")

        payloads = build_optimized_translations(self.reader, entries, "tr")
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["locale"], "tr")
        elements = read_elements(
            payloads[0]["hash_table"], payloads[0]["bucket_table"], base64.b64decode(str(payloads[0]["strings"]))
        )
        self.assertEqual([e.text for e in elements], ["Oyunu Başlat", "the and"])

        messages = build_runtime_messages(entries)
        self.assertEqual(messages["MENU_EXIT"], "Çıkış")  # plain Translation keeps its key

        game_dir = self.dir / "game"
        game_dir.mkdir()
        OverlayPatcher(game_dir).build_overlay_patch(
            {}, target_locale="tr", runtime_translations=messages, optimized_translations=payloads
        )
        patch = PCKReader(game_dir / OverlayPatcher.DEFAULT_PATCH_PCK)
        patch.open()
        data = json.loads(patch.read_file_bytes(TRANSLATIONS_RES_PATH))
        self.assertEqual(data["optimized"][0]["locale"], "tr")
        script = (game_dir / OverlayPatcher.BOOTSTRAP_SCRIPT_NAME).read_text(encoding="utf-8")
        self.assertIn("OptimizedTranslation.new()", script)


if __name__ == "__main__":
    unittest.main()
