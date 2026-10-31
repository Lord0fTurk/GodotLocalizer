"""Every UI language must define the same keys as English and keep its placeholders."""

import json
import re
import unittest
from pathlib import Path

I18N_DIR = Path(__file__).resolve().parents[1] / "src" / "gui" / "i18n"
PLACEHOLDER = re.compile(r"\{\w+\}|%[sd]")
LEFTOVERS = re.compile(r"Unity|BepInEx|XUnity|IL2CPP|TextMeshPro|Assembly-CSharp")


def _flat(data: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            out.update(_flat(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


def _load(code: str) -> dict[str, str]:
    return _flat(json.loads((I18N_DIR / f"{code}.json").read_text(encoding="utf-8")))


class TestI18nConsistency(unittest.TestCase):
    def setUp(self) -> None:
        self.english = _load("en")
        self.codes = sorted(p.stem for p in I18N_DIR.glob("*.json") if p.stem != "en")

    def test_every_language_has_every_english_key(self) -> None:
        for code in self.codes:
            missing = sorted(set(self.english) - set(_load(code)))
            self.assertFalse(missing, f"{code} is missing keys: {missing[:5]}")

    def test_no_language_has_keys_english_lacks(self) -> None:
        for code in self.codes:
            extra = sorted(set(_load(code)) - set(self.english))
            self.assertFalse(extra, f"{code} has stale keys: {extra[:5]}")

    def test_placeholders_are_preserved(self) -> None:
        for code in self.codes:
            for key, text in _load(code).items():
                expected = sorted(PLACEHOLDER.findall(self.english.get(key, "")))
                self.assertEqual(expected, sorted(PLACEHOLDER.findall(text)), f"{code}:{key}")

    def test_no_unity_leftovers_in_any_language(self) -> None:
        for code in ["en", *self.codes]:
            for key, text in _load(code).items():
                if key == "about.description":  # names the sister projects on purpose
                    continue
                self.assertIsNone(LEFTOVERS.search(text), f"{code}:{key}: {text}")


if __name__ == "__main__":
    unittest.main()
