"""Dialogic 2 text events without a speaker, with BBCode or variables are still dialogue."""

import unittest

from src.parsers.dialogic import DialogicParser

SOURCE = (
    "join Rose (Default) right\n"
    "label start\n"
    "You lie down on the bed.\n"
    "{Rose} steps closer.\n"
    "????: Who are you?\n"
    "Apherya: [wave amp=50][b]Finally![/b][/wave]\n"
    '[color="red"] Select all cards!\n'
    '[wait time="1"]\n'
    "[end_timeline]\n"
    "do QuestManager.start(1)\n"
    "if {x}:\n"
    "\tShe waits #id:12\n"
    "# a comment\n"
    "...\n"
    "- Go on\n"
)


class TestDialogicNarration(unittest.TestCase):
    def test_harvests_narration_but_not_commands(self) -> None:
        texts = [e.source_text for e in DialogicParser.parse_dtl(SOURCE, "res://t.dtl")]
        self.assertEqual(texts, [
            "You lie down on the bed.",
            "{Rose} steps closer.",
            "Who are you?",
            "[wave amp=50][b]Finally![/b][/wave]",
            '[color="red"] Select all cards!',
            "She waits",
            "Go on",
        ])

    def test_roundtrip_touches_only_the_text(self) -> None:
        entries = DialogicParser.parse_dtl(SOURCE, "res://t.dtl")
        lookup = {e.key: f"TR<{e.line_number}>" for e in entries}
        out = DialogicParser.apply_dtl_translations(SOURCE, lookup, "res://t.dtl").splitlines()
        self.assertEqual(out[2], "TR<3>")
        self.assertEqual(out[4], "????: TR<5>")
        self.assertEqual(out[7], '[wait time="1"]')
        self.assertEqual(out[9], "do QuestManager.start(1)")
        self.assertEqual(out[11], "\tTR<12> #id:12")
        self.assertEqual(out[13], "...")


if __name__ == "__main__":
    unittest.main()
