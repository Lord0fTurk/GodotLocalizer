"""Dialogic inline effects ([pause=0.5s] ...) must reach the game untouched, even from the cache."""

import tempfile
import unittest
from pathlib import Path

from src.core.cache import TranslationCache
from src.core.syntax_guard import UnitySyntaxGuard
from src.core.translators.base import TranslationEngine
from src.core.translators.manager import TranslationManager
from src.utils.config import ConfigManager

SOURCE = "Something is there[pause=0.5s].[pause=0.5s].[pause=0.5s]."
MANGLED = "Bir şey var[duraklatma=0,5sn].[duraklatma=0,5sn].[duraklatma=0,5sn]."


class TestDialogicInlineTags(unittest.TestCase):
    def test_pause_tags_are_protected(self) -> None:
        _protected, tokens = UnitySyntaxGuard.protect(SOURCE)
        self.assertEqual(tokens, ["[pause=0.5s]"] * 3)

    def test_subscripted_variable_is_protected(self) -> None:
        text = 'Lust: {Save.dict["characters"]["Morgan"]["lust_bar_value"]}'
        self.assertEqual(UnitySyntaxGuard.protect(text)[1], ['{Save.dict["characters"]["Morgan"]["lust_bar_value"]}'])

    def test_translated_pause_tag_is_rejected(self) -> None:
        ok, _errors = UnitySyntaxGuard.validate_integrity(SOURCE, MANGLED)
        self.assertFalse(ok)
        ok, _errors = UnitySyntaxGuard.validate_integrity(SOURCE, "Bir şey var[pause=0.5s].[pause=0.5s].[pause=0.5s].")
        self.assertTrue(ok)

    def test_mangled_cache_entry_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = TranslationCache(Path(tmp) / "t.db")
            manager = TranslationManager(config=ConfigManager(Path(tmp) / "c.json", vault=None), cache=cache)
            results: list = [None]
            engine = TranslationEngine.GOOGLE
            self.assertFalse(manager._resolve_offline(0, SOURCE, {SOURCE: MANGLED}, results, "en", "tr", engine))
            good = "Bir şey var[pause=0.5s].[pause=0.5s].[pause=0.5s]."
            self.assertTrue(manager._resolve_offline(0, SOURCE, {SOURCE: good}, results, "en", "tr", engine))
            self.assertEqual(results[0].translated_text, good)


if __name__ == "__main__":
    unittest.main()
