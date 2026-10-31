"""Internationalization manager supporting multi-language dynamic switching."""

import json
from pathlib import Path
from typing import Any, ClassVar

from src.utils.logger import logger


class I18nManager:
    """Loads JSON language dictionaries and translates UI strings dynamically."""

    DEFAULT_LOCALE = "tr"
    SUPPORTED_LOCALES: ClassVar[list[tuple[str, str]]] = [
        ("tr", "Türkçe"),
        ("en", "English"),
        ("de", "Deutsch"),
        ("fr", "Français"),
        ("es", "Español"),
        ("ru", "Русский"),
        ("ja", "日本語"),
        ("zh-CN", "简体中文"),
    ]

    def __init__(self, locales_dir: Path | None = None) -> None:
        if locales_dir is None:
            self.locales_dir = Path(__file__).resolve().parent
        else:
            self.locales_dir = Path(locales_dir).resolve()

        self.current_locale: str = self.DEFAULT_LOCALE
        self._catalogs: dict[str, dict[str, Any]] = {}
        self.load_all_catalogs()

    def load_all_catalogs(self) -> None:
        """Pre-loads all JSON language dictionary files in the locales folder."""
        for code, _ in self.SUPPORTED_LOCALES:
            file_path = self.locales_dir / f"{code}.json"
            if file_path.exists():
                try:
                    with open(file_path, encoding="utf-8") as f:
                        self._catalogs[code] = json.load(f)
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"Failed loading locale file '{code}.json': {e}")
            else:
                self._catalogs[code] = {}

    def set_locale(self, locale_code: str) -> bool:
        """Switches active UI language."""
        if locale_code in self._catalogs:
            self.current_locale = locale_code
            logger.info(f"UI language set to '{locale_code}'.")
            return True
        logger.warning(f"Unsupported locale code requested: '{locale_code}'.")
        return False

    def t(self, key_path: str, default: str = "") -> str:
        """Translates a dot-separated key (e.g. 'nav.dashboard') to active language."""
        parts = key_path.split(".")

        # 1. Lookup in current locale
        val = self._resolve_key(self.current_locale, parts)
        if val is not None:
            return str(val)

        # 2. Fallback to English
        val_en = self._resolve_key("en", parts)
        if val_en is not None:
            return str(val_en)

        # 3. Fallback to Turkish
        val_tr = self._resolve_key("tr", parts)
        if val_tr is not None:
            return str(val_tr)

        return default or key_path

    def _resolve_key(self, locale: str, parts: list[str]) -> Any | None:
        """Traverses nested dictionary hierarchy."""
        catalog = self._catalogs.get(locale, {})
        curr = catalog
        for p in parts:
            if isinstance(curr, dict) and p in curr:
                curr = curr[p]
            else:
                return None
        return curr
