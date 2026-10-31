"""Bilingual display: appends the original only where it cannot break formatting."""

import unittest

from src.core.bilingual import DEFAULT_TEMPLATE, combine, normalize_template
from src.core.pipeline.orchestrator import GodotTranslationPipeline
from src.harvesters.base import HarvestedEntry, StringSourceType


class TestCombine(unittest.TestCase):
    def test_plain_text_gets_the_original_below(self) -> None:
        self.assertEqual(combine("Start Game", "Oyunu Başlat"), "Oyunu Başlat\n(Start Game)")
        self.assertEqual(combine("Hi", "Selam", "{source} → {target}"), "Hi → Selam")

    def test_anything_with_formatting_stays_single_language(self) -> None:
        for source, translation in (
            ("Score: %d", "Puan: %d"),
            ("%05d points", "%05d puan"),
            ("Hello {name}", "Merhaba {name}"),
            ("[color=red]Danger[/color]", "[color=red]Tehlike[/color]"),
            ("Hi {{player.name}}", "Selam {{player.name}}"),
            ("100%% done", "%%100 tamam"),
        ):
            self.assertEqual(combine(source, translation), translation, source)

    def test_untranslated_or_identical_text_is_left_alone(self) -> None:
        self.assertEqual(combine("OK", "OK"), "OK")
        self.assertEqual(combine("Exit", ""), "")

    def test_a_template_missing_a_field_falls_back_to_the_default(self) -> None:
        self.assertEqual(normalize_template("{target} only"), "{target}\n({source})")
        self.assertEqual(combine("A b", "C d", "{target} only"), "C d\n(A b)")
        self.assertEqual(combine("A b", "C d", "{source} / {target}"), "A b / C d")

    def test_a_typed_backslash_n_is_a_line_break(self) -> None:
        """The setting is a one-line field, so users (and the default) write the line break as backslash-n."""
        self.assertEqual(DEFAULT_TEMPLATE, "{target}\\n({source})")
        self.assertEqual(combine("A b", "C d", "{target}\\n[{source}]"), "C d\n[A b]")
        self.assertEqual(combine("A b", "C d", DEFAULT_TEMPLATE), "C d\n(A b)")


class TestPipelineIntegration(unittest.TestCase):
    @staticmethod
    def entries() -> tuple[dict[str, HarvestedEntry], dict[str, list[str]]]:
        by_key = {
            k: HarvestedEntry(key=k, source_text=t, source_file="res://a.scn", source_type=StringSourceType.SCENE_UI)
            for k, t in (("a", "Start Game"), ("b", "Score: %d"))
        }
        return by_key, {"Start Game": ["a"], "Score: %d": ["b"]}

    def run_apply(self, bilingual: bool, target: str = "tr") -> dict[str, str]:
        pipeline = GodotTranslationPipeline.__new__(GodotTranslationPipeline)
        pipeline.bilingual, pipeline.bilingual_template, pipeline.rtl_shaping = bilingual, DEFAULT_TEMPLATE, True
        by_key, unique = self.entries()
        pipeline._apply_translations(by_key, unique, {"Start Game": "Başla", "Score: %d": "Puan: %d"}, target, True)
        return {k: e.translation for k, e in by_key.items()}

    def test_setting_controls_the_result(self) -> None:
        self.assertEqual(self.run_apply(False), {"a": "Başla", "b": "Puan: %d"})
        self.assertEqual(self.run_apply(True), {"a": "Başla\n(Start Game)", "b": "Puan: %d"})

    def test_right_to_left_targets_are_never_mixed(self) -> None:
        self.assertEqual(self.run_apply(True, "ar")["a"], "Başla")


if __name__ == "__main__":
    unittest.main()
