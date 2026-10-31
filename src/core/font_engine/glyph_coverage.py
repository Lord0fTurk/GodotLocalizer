"""Inspects TrueType and OpenType fonts for character glyph coverage."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from fontTools.ttLib import TTFont


@dataclass(slots=True)
class CoverageReport:
    """Detailed report on font glyph coverage for a given target charset."""

    target_lang: str
    total_required: int
    covered_count: int
    missing_chars: list[str]

    @property
    def coverage_ratio(self) -> float:
        """Percentage ratio of covered characters (0.0 - 1.0)."""
        if self.total_required == 0:
            return 1.0
        return self.covered_count / self.total_required

    @property
    def is_fully_covered(self) -> bool:
        """True if all required characters in the charset exist in the font."""
        return len(self.missing_chars) == 0


_PUNCT = "0123456789.,!?'\"-;:()"
_LATIN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _span(first: int, last: int) -> str:
    return "".join(chr(c) for c in range(first, last + 1))


# Representative (not exhaustive) character sets for scripts that fonts commonly lack.
_EXTRA_CHARSETS: dict[str, set[str]] = {
    "pt": set(_LATIN + "áâãàçéêíóôõúüÁÂÃÀÇÉÊÍÓÔÕÚÜ" + _PUNCT),
    "it": set(_LATIN + "àèéìíîòóùúÀÈÉÌÍÎÒÓÙÚ" + _PUNCT),
    "pl": set(_LATIN + "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ" + _PUNCT),
    "uk": set("абвгдеєжзиіїйклмнопрстуфхцчшщьюяАБВГДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯґҐ" + _PUNCT),
    "ar": set(_span(0x0621, 0x063A) + _span(0x0641, 0x064A) + _PUNCT),
    "fa": set(_span(0x0621, 0x063A) + _span(0x0641, 0x064A) + "پچژگکی" + _PUNCT),
    "he": set(_span(0x05D0, 0x05EA) + _PUNCT),
    "ja": set(_span(0x3041, 0x3093) + _span(0x30A1, 0x30F6) + "日本語人大学生時間年月私今何" + _PUNCT),
    "zh": set("的一是不了人我在有他这中大来上国个到说们为子和你地出道也时年得就那要下以生会自着去之过家学对可她里后小么心多天而能好都然没日于起还发成事只作当想看文无开手十用主行方又如前所本见经头面公同三已老从动两长知民样现分将外但身些与高意进把法此实回二理美点月明其种声全工己话儿者向情部正名定女问力机给等几很业最间新什打便位因重被走电四第门相次东政海口使教西再平真听世气信北少关并内加化由却代军产入先山无它" + _PUNCT),
    "ko": set(_span(0xAC00, 0xAC30) + "한국어를은는이가에서의로도과와하다있없" + _PUNCT),
}


class GlyphCoverageChecker:
    """Analyzes font CMAP tables to guarantee no tofu glyphs occur in localized text."""

    LANGUAGE_CHARSETS: ClassVar[dict[str, set[str]]] = {
        **_EXTRA_CHARSETS,
        "tr": set("abcçdefgğhıijklmnoöprsştuüvyzABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ0123456789.,!?'\"-;:()"),
        "ru": set("абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ0123456789.,!?'\"-;:()"),
        "de": set("abcdefghijklmnopqrstuvwxyzäöüßABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜ0123456789.,!?'\"-;:()"),
        "fr": set("abcdefghijklmnopqrstuvwxyzàâæçéèêëîïôœùûüÿÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ0123456789.,!?'\"-;:()"),
        "es": set("abcdefghijklmnñopqrstuvwxyzáéíóúüñ¿¡ABCDEFGHIJKLMNÑOPQRSTUVWXYZÁÉÍÓÚÜÑ0123456789.,!?'\"-;:()"),
    }

    @classmethod
    def check_font_file(cls, font_path: str | Path, lang_code: str = "tr") -> CoverageReport:
        """Reads font cmap tables and checks coverage against standard character sets."""
        path = Path(font_path)
        if not path.exists():
            raise FileNotFoundError(f"Font file not found: {font_path}")

        tt = TTFont(str(path))
        unicode_codepoints: set[int] = set()

        for table in tt["cmap"].tables:
            if table.isUnicode():
                unicode_codepoints.update(table.cmap.keys())

        key = lang_code.lower().replace("_", "-").split("-")[0]
        required_chars = cls.LANGUAGE_CHARSETS.get(key)
        if not required_chars:
            # A wrong charset would report "fully covered" for scripts we do not check at all.
            raise ValueError(f"No glyph charset defined for language '{lang_code}'.")

        missing: list[str] = []
        covered: int = 0

        for ch in sorted(required_chars):
            if ord(ch) in unicode_codepoints:
                covered += 1
            else:
                missing.append(ch)

        return CoverageReport(
            target_lang=lang_code,
            total_required=len(required_chars),
            covered_count=covered,
            missing_chars=missing,
        )
