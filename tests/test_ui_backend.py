"""Unit tests for backend bridge, settings coercion, cache, glossary, and review editor."""

import tempfile
import unittest
from pathlib import Path

from PyQt6.QtCore import QCoreApplication

from src.backend.app_backend import AppBackend
from src.backend.editor_backend import EditorBackend
from src.backend.settings_schema import coerce_setting
from src.core.cache import TranslationCache
from src.core.glossary import GlossaryManager
from src.core.pipeline.review_session import ReviewRow, ReviewSession, RunContext
from src.core.syntax_guard import GodotSyntaxGuard


class TestBackendAndCoreServices(unittest.TestCase):
    """Verifies core translation helpers, caching, and QObject backend bridges."""

    @classmethod
    def setUpClass(cls) -> None:
        if not QCoreApplication.instance():
            cls.app = QCoreApplication([])

    def test_settings_coercion(self) -> None:
        """Asserts settings values are properly typed and bounded."""
        self.assertEqual(coerce_setting("max_concurrency", "8"), 8)
        self.assertEqual(coerce_setting("max_concurrency", 999), 32)
        self.assertEqual(coerce_setting("max_concurrency", 0), 1)
        self.assertEqual(coerce_setting("batch_size", "25"), 25)
        self.assertTrue(coerce_setting("use_cache", "true"))
        self.assertFalse(coerce_setting("use_cache", 0))

    def test_translation_cache_sqlite(self) -> None:
        """Verifies SQLite persistent cache roundtrip and count."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_cache.db"
            cache = TranslationCache(db_path=db_path)
            self.assertEqual(cache.count(), 0)

            cache.set("Hello World", "Merhaba Dünya", "en", "tr", "google")
            self.assertEqual(cache.count(), 1)
            self.assertEqual(cache.get("Hello World", "en", "tr", "google"), "Merhaba Dünya")
            self.assertIsNone(cache.get("Hello World", "en", "de", "google"))

            cache.clear()
            self.assertEqual(cache.count(), 0)

    def test_glossary_protection_and_application(self) -> None:
        """Verifies terms are safely applied to translated text."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            glossary_path = Path(tmp_dir) / "glossary.json"
            mgr = GlossaryManager(glossary_path=glossary_path)
            mgr.add_term("Godot Engine", "Godot Motoru")
            mgr.add_term("Quest", "Görev")

            applied = mgr.apply("Welcome to Godot Engine! Your Quest begins.")
            self.assertIn("Godot Motoru", applied)
            self.assertIn("Görev", applied)

            self.assertTrue(mgr.remove_term("Quest"))
            self.assertEqual(len(mgr.load()), 1)

    def test_syntax_guard_bbcode_protection(self) -> None:
        """Verifies BBCode tags and format specifiers are extracted and restored."""
        text = "Press [b]Start[/b] to begin, player %s!"
        masked, tokens = GodotSyntaxGuard.protect(text)

        self.assertNotIn("[b]", masked)
        self.assertNotIn("[/b]", masked)
        self.assertNotIn("%s", masked)
        self.assertEqual(len(tokens), 3)

        restored = GodotSyntaxGuard.restore(masked, tokens)
        self.assertEqual(restored, text)

    def test_editor_backend_session_workflow(self) -> None:
        """Verifies review session row inspection and manual editing."""
        editor = EditorBackend()
        self.assertFalse(editor.hasSession)

        ctx = RunContext(
            game_root=Path("."),
            target_lang="tr",
            source_lang="en",
            engine="google",
        )
        row = ReviewRow(
            row_id=0,
            original="Hello [b]World[/b]",
            translation="Merhaba [b]Dünya[/b]",
            status="accepted",
        )
        session = ReviewSession(rows=[row], context=ctx)
        editor.loadSession(session)

        self.assertTrue(editor.hasSession)
        self.assertEqual(editor.totalCount, 1)
        self.assertEqual(editor.targetLanguage, "tr")

        editor.updateTranslation(0, "Selam [b]Dünya[/b]")
        self.assertEqual(editor.filteredRows[0]["translation"], "Selam [b]Dünya[/b]")

    def test_app_backend_properties(self) -> None:
        """Verifies AppBackend reactive properties and engines list."""
        backend = AppBackend()
        self.assertIn("google", backend.selectableEngines)
        self.assertIn("openai", backend.selectableEngines)
        self.assertIn("gemini", backend.selectableEngines)
        self.assertGreater(len(backend.supportedEngines), 0)
        self.assertGreater(len(backend.availableLanguages), 0)

        backend.setTargetLanguage("tr")
        self.assertEqual(backend.targetLanguage, "tr")
        backend.setEngine("google")
        self.assertEqual(backend.engine, "google")

        # Verify glossary and cache properties
        self.assertIsInstance(backend.glossaryTerms, list)
        self.assertIsInstance(backend.cacheEntryCount, int)
        backend.addGlossaryTerm("Godot", "GodotEngine")
        self.assertTrue(any(t["term"] == "Godot" for t in backend.glossaryTerms))
        backend.removeGlossaryTerm("Godot")


if __name__ == "__main__":
    unittest.main()
