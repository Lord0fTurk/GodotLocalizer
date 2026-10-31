"""Engines rewrite the ⟦ULPH_n⟧ sentinel in many ways; every slot must still come back or be rejected."""

import unittest

from src.core.syntax_guard import UnitySyntaxGuard

SOURCE = "Hello, [player]."


class TestPlaceholderRecovery(unittest.TestCase):
    def setUp(self) -> None:
        self.protected, self.tags = UnitySyntaxGuard.protect(SOURCE)

    def test_renamed_sentinel_is_restored_by_index(self) -> None:
        for mangled in (
            "⟦ulf_0⟧", "⟦ UPLH-0 ⟧", "⟦ULPH_0⟧", "[ULPH_0⟧", "⟦ulph_0]", "(ULPH_0)", "ULPH_0", "ULPH 0",
            "ULPH_0⟧", "⟦ulf_0", "&#10214;ULPH_0&#10215;", "⟦УЛПХ_0⟧",
        ):
            restored = UnitySyntaxGuard.restore(f"Merhaba, {mangled}.", self.tags)
            self.assertEqual(restored, "Merhaba, [player].", mangled)
            self.assertTrue(UnitySyntaxGuard.validate_integrity(SOURCE, restored)[0], mangled)

    def test_unrecoverable_sentinel_is_rejected_not_repaired_around(self) -> None:
        for broken in ("Merhaba, ⟦9⟧.", "Merhaba, ulf_0.", "Merhaba, ⟦ulf⟧."):
            restored = UnitySyntaxGuard.restore(broken, self.tags)
            self.assertFalse(UnitySyntaxGuard.validate_integrity(SOURCE, restored)[0], broken)
            self.assertIsNone(UnitySyntaxGuard.repair_missing_tokens(SOURCE, restored), broken)

    def test_sentinel_characters_in_the_source_are_not_flagged(self) -> None:
        text = "Set A ⟦x⟧ to 1"
        self.assertTrue(UnitySyntaxGuard.validate_integrity(text, text)[0])


if __name__ == "__main__":
    unittest.main()
