"""Pre-flight analysis: asks the user only when the package needs a decision (key, JSON fields, old scripts)."""

import json
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QCoreApplication

from src.backend.app_backend import AppBackend
from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter
from src.core.pipeline.analysis import count_unreadable_scripts, find_json_key_candidates, looks_like_prose
from src.utils.config import ConfigManager

KEY = "00112233445566778899aabbccddeeff" * 2
CARDS = {"cards": [{"id": f"c{i}", "card_text": f"Deal {i} damage to the nearest enemy", "title": f"Card {i}"} for i in range(8)]}


SHOUTS = ["Fire in the hole", "Watch your back", "Over my dead body", "Not on my watch", "Hold the line", "Stay behind me",
          "Keep your distance", "Eyes on the road", "Never again today", "Come get some", "Right behind you", "Take the shot",
          "One more round", "Leave no trace", "Make it count", "Hands off me"]
AMBIGUOUS = {"lines": [{"id": f"s{i}", "shout": text} for i, text in enumerate(SHOUTS)]}


def build_pack(path: Path, *, json_data: object = None, old_script: bool = False, encrypted: bool = False) -> Path:
    writer = PCKWriter(path, engine_version=(4, 2, 0), encryption_key=KEY if encrypted else None,
                       encrypt_directory=encrypted)
    writer.add_bytes("res://readme.txt", b"hello", encrypted=encrypted)
    if json_data is not None:
        writer.add_bytes("res://data/cards.json", json.dumps(json_data).encode(), encrypted=encrypted)
    if old_script:
        writer.add_bytes("res://old.gdc", b"GDSC" + struct.pack("<II", 13, 0) + b"\x00" * 8, encrypted=encrypted)
    writer.build()
    return path


class TestAnalysis(unittest.TestCase):
    def test_prose_heuristic(self) -> None:
        for text in ("Deal 6 damage to all enemies", "Welcome back, traveler"):
            self.assertTrue(looks_like_prose(text), text)
        for text in ("res://a/b.tscn", "player_hp", "enemy_01", "12.5", "ab", "Hero"):
            self.assertFalse(looks_like_prose(text), text)

    def test_candidates_exclude_known_fields_and_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reader = PCKReader(build_pack(Path(tmp) / "g.pck", json_data=CARDS))
            reader.open()
            found = find_json_key_candidates(reader)
            self.assertEqual([(c.key, c.count) for c in found], [("card_text", 8)])  # `title`/`id` are not offered
            self.assertEqual(find_json_key_candidates(reader, frozenset({"card_text"})), [])

    @staticmethod
    def reader_for(tmp: str, data: object) -> PCKReader:
        reader = PCKReader(build_pack(Path(tmp) / "g.pck", json_data=data))
        reader.open()
        return reader

    def test_sentence_fields_are_recommended_without_a_question(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (card,) = find_json_key_candidates(self.reader_for(tmp, CARDS))
        self.assertEqual((card.key, card.reason, card.selected, card.needs_question), ("card_text", "prose", True, False))

    def test_a_localization_table_is_understood_without_asking(self) -> None:
        english = ["The old king wants to see you in the hall", "You will not find the sword that you are looking for",
                   "It was the best of the days that they had", "I have a bad feeling about this place and the people",
                   "What do you want from me in this town", "She said that the road to the city was closed"]
        german = ["Der alte König möchte dich in der Halle sehen", "Das Schwert, das du suchst, wirst du nicht finden",
                  "Es war der beste der Tage, die sie hatten", "Ich habe ein schlechtes Gefühl bei diesem Ort und den Leuten",
                  "Was willst du von mir in dieser Stadt", "Sie sagte, dass die Straße zur Stadt gesperrt war"]
        table = [{"source": s, "target": g} for s, g in zip(english, german)]
        with tempfile.TemporaryDirectory() as tmp:
            found = {c.key: c for c in find_json_key_candidates(self.reader_for(tmp, {"rows": table}))}
        self.assertEqual((found["source"].reason, found["source"].selected), ("table_source", True))
        self.assertEqual((found["target"].reason, found["target"].selected), ("table_translated", False))
        self.assertFalse(any(c.needs_question for c in found.values()))

    def test_two_columns_in_the_same_language_are_a_question(self) -> None:
        rows = [{"short": f"Deal {i} damage to the first enemy that you can see", "long": f"This card will deal {i} damage to the enemy and then stop"} for i in range(8)]
        with tempfile.TemporaryDirectory() as tmp:
            found = find_json_key_candidates(self.reader_for(tmp, {"rows": rows}))
        self.assertEqual({c.reason for c in found}, {"table_unclear"})
        self.assertTrue(all(c.needs_question and not c.selected for c in found))

    def test_names_and_placeholders_are_ignored(self) -> None:
        names = {"people": [{"id": f"p{i}", "alias": n} for i, n in enumerate(
            ["Bay Wilde", "Temperance Cole", "Doki Hart", "Old Man Jenkins", "Sister Agnes", "Captain Reyes", "Mister Fox"])],
                 "placeholders": [{"id": f"x{i}", "memo": "Add Text Here"} for i in range(9)]}
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_json_key_candidates(self.reader_for(tmp, names)), [])

    def test_short_phrases_are_a_question_not_a_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (shout,) = find_json_key_candidates(self.reader_for(tmp, AMBIGUOUS))
        self.assertEqual((shout.reason, shout.selected, shout.needs_question), ("maybe", False, True))

    def test_legacy_script_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reader = PCKReader(build_pack(Path(tmp) / "g.pck", old_script=True))
            reader.open()
            self.assertEqual(count_unreadable_scripts(reader), 1)


class TestBackendFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        with mock.patch("src.backend.app_backend.ConfigManager", lambda: ConfigManager(self.dir / "c.json", vault=None)):
            self.backend = AppBackend()
        self.began = threading.Event()
        self.backend._begin_translation = self.began.set  # type: ignore[method-assign]
        self.events: list[tuple[str, object]] = []
        self.backend.pckKeyNeeded.connect(lambda r: self.events.append(("key", r)))
        self.backend.jsonKeysSuggested.connect(lambda c: self.events.append(("json", c)))
        self.backend.legacyScriptsFound.connect(lambda n: self.events.append(("legacy", n)))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def wait(self, timeout: float = 5.0) -> None:
        deadline = threading.Event()
        for _ in range(int(timeout / 0.02)):
            QCoreApplication.processEvents()
            if self.events or self.began.is_set():
                return
            deadline.wait(0.02)

    def test_encrypted_game_asks_for_the_key_then_continues(self) -> None:
        pack = build_pack(self.dir / "g.pck", encrypted=True)
        self.backend.selectGame(str(pack))
        self.assertEqual(self.events[-1], ("key", "missing"))
        self.backend.submitPckKey("ff" * 32)
        self.assertEqual(self.events[-1], ("key", "wrong"))
        self.backend.submitPckKey("not-hex")
        self.assertEqual(self.events[-1], ("key", "invalid"))
        self.backend.submitPckKey(KEY)
        self.assertEqual(len(self.events), 3)  # no new prompt: the game opened
        self.assertEqual(self.backend.gamePath, str(pack.resolve()))

    def test_confident_json_fields_are_added_without_asking(self) -> None:
        pack = build_pack(self.dir / "g.pck", json_data=CARDS, old_script=True)
        self.backend.selectGame(str(pack))
        self.backend.startTranslation()
        self.wait()
        self.assertEqual(self.events, [("legacy", 1)])  # no JSON question: sentences are obviously text
        self.assertEqual(self.backend.config.get("json_text_keys"), "card_text")
        self.events.clear()
        self.backend.answerLegacyScripts(False)
        self.wait()
        self.assertTrue(self.began.is_set(), "translation should start once every question is answered")
        self.assertEqual(self.events, [])

    def test_unclear_fields_are_asked_with_a_reason_and_a_recommendation(self) -> None:
        pack = build_pack(self.dir / "g.pck", json_data=AMBIGUOUS)
        self.backend.selectGame(str(pack))
        self.backend.startTranslation()
        self.wait()
        kind, candidates = self.events[-1]
        self.assertEqual(kind, "json")
        by_key = {c["key"]: c for c in candidates}  # type: ignore[index]
        self.assertEqual((by_key["shout"]["reason"], by_key["shout"]["selected"], by_key["shout"]["ask"]), ("maybe", False, True))
        self.assertEqual(self.backend.config.get("json_text_keys"), "")  # nothing is decided behind the user's back

        self.events.clear()
        self.backend.applyJsonKeys(["shout"])
        self.wait()
        self.assertEqual(self.backend.config.get("json_text_keys"), "shout")
        self.assertTrue(self.began.is_set())

    def test_nothing_to_ask_starts_immediately(self) -> None:
        pack = build_pack(self.dir / "g.pck")
        self.backend.selectGame(str(pack))
        self.backend.startTranslation()
        self.wait()
        self.assertTrue(self.began.is_set())
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
