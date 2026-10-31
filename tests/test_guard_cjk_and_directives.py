"""Ports from UnityLocalizer's guard: CJK bracket swaps, [name=value] directives, names beside kana/Han."""

import unittest

from src.core.syntax_guard import UnitySyntaxGuard, set_protected_terms

SOURCE = "Hello, [player]."


class TestCjkBracketRecovery(unittest.TestCase):
    def setUp(self) -> None:
        self.tags = UnitySyntaxGuard.protect(SOURCE)[1]

    def test_swapped_brackets_are_consumed_with_the_token(self) -> None:
        for mangled in ("「ULPH_0」", "［ULPH_0］", "【ULPH＿０】", "（ULPH_0）", "《 ULPH-0 》"):
            restored = UnitySyntaxGuard.restore(f"こんにちは、{mangled}。", self.tags)
            self.assertEqual(restored, "こんにちは、[player]。", mangled)

    def test_a_legitimate_quote_next_to_a_token_is_kept(self) -> None:
        restored = UnitySyntaxGuard.restore("「こんにちは」 ⟦ULPH_0⟧", self.tags)
        self.assertEqual(restored, "「こんにちは」 [player]")


class TestDirectivesAndTerms(unittest.TestCase):
    def tearDown(self) -> None:
        set_protected_terms([])

    def test_name_equals_value_directives_are_protected(self) -> None:
        self.assertEqual(UnitySyntaxGuard.protect("Wait[wait=0.5] now [pic=39]")[1], ["[wait=0.5]", "[pic=39]"])

    def test_protected_name_is_found_beside_kana(self) -> None:
        set_protected_terms(["Alice"])
        self.assertEqual(UnitySyntaxGuard.protect("Aliceは勇者です")[1], ["Alice"])
        self.assertEqual(UnitySyntaxGuard.protect("Alice is here")[1], ["Alice"])
        self.assertEqual(UnitySyntaxGuard.protect("Malice is here")[1], [])


if __name__ == "__main__":
    unittest.main()
