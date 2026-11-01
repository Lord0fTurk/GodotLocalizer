"""Typed schema for user-editable settings exposed to QML through ``AppBackend.setSetting``."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from src.core.translators.ai_translator import GEMINI_SAFETY_LEVELS
from src.core.translators.deepl import FORMALITY_VALUES


@dataclass(frozen=True)
class SettingSpec:
    """Type and constraints of one setting."""

    kind: type
    low: float | None = None
    high: float | None = None
    choices: tuple[str, ...] = ()


def _int(low: float, high: float) -> SettingSpec:
    return SettingSpec(int, low, high)


def _float(low: float, high: float) -> SettingSpec:
    return SettingSpec(float, low, high)


_BOOL = SettingSpec(bool)
_TEXT = SettingSpec(str)

SETTING_SPECS: dict[str, SettingSpec] = {
    # Performance & network
    "batch_size": _int(10, 500),
    "max_concurrency": _int(1, 32),
    "rate_limit_delay": _float(0.0, 10.0),
    "request_timeout": _int(5, 300),
    "max_retries": _int(1, 10),
    "max_chars_per_request": _int(500, 20000),
    "use_multi_endpoint": _BOOL,
    "lingva_fallback": _BOOL,
    "browser_impersonation": _BOOL,
    "aggressive_retry": _BOOL,
    "proxy_enabled": _BOOL,
    "proxy_list": _TEXT,
    "use_cache": _BOOL,
    "custom_font_path": _TEXT,
    "font_preset": SettingSpec(str, choices=("default_arial", "custom_ttf", "system_font", "noto_sans", "custom")),
    "theme_mode": SettingSpec(str, choices=("obsidian", "daylight", "cyber_neon")),
    "accent_preset": SettingSpec(str, choices=("cyan", "emerald", "indigo", "amber")),
    "bilingual_mode": _BOOL,
    "bilingual_template": _TEXT,
    "patch_strategy": SettingSpec(str, choices=("overlay", "repack")),
    "pck_encryption_key": _TEXT,
    "gdre_path": _TEXT,
    "json_text_keys": _TEXT,
    # Engines
    "default_engine": SettingSpec(str, choices=("google", "bing", "deepl", "gemini", "openai", "claude", "deepseek", "local_llm", "libretranslate")),
    "deepl_key": _TEXT,
    "deepl_formality": SettingSpec(str, choices=tuple(FORMALITY_VALUES)),
    "gemini_key": _TEXT,
    "gemini_model": _TEXT,
    "gemini_safety": SettingSpec(str, choices=GEMINI_SAFETY_LEVELS),
    "openai_key": _TEXT,
    "openai_model": _TEXT,
    "openai_base_url": _TEXT,
    "deepseek_key": _TEXT,
    "deepseek_model": _TEXT,
    "local_llm_url": _TEXT,
    "local_llm_model": _TEXT,
    "local_llm_timeout": _int(10, 1800),
    "libretranslate_url": _TEXT,
    "libretranslate_key": _TEXT,
    "show_debug_engines": _BOOL,
    # AI generation
    "ai_temperature": _float(0.0, 1.0),
    "ai_max_tokens": _int(256, 65536),
    "ai_timeout": _int(10, 1800),
    "ai_retries": _int(0, 5),
    "ai_request_delay": _float(0.0, 30.0),
    "ai_batch_size": _int(1, 50),
    "ai_concurrency": _int(1, 16),
    "ai_custom_prompt": _TEXT,
    # Terminology & memory
    "protected_terms": _TEXT,
    "tm_enabled": _BOOL,
    "tm_files": _TEXT,
    "tm_fuzzy": _BOOL,
    "tm_fuzzy_threshold": _float(0.5, 1.0),
}


def coerce_setting(key: str, value: Any) -> Any:
    """Validated value for key; raises KeyError for unknown keys, ValueError for bad values."""
    spec = SETTING_SPECS[key]
    if spec.kind is bool:
        return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
    if spec.kind is int:
        n = int(value)
        if spec.low is not None:
            n = max(int(spec.low), n)
        if spec.high is not None:
            n = min(int(spec.high), n)
        return n
    if spec.kind is float:
        f = float(value)
        if not math.isfinite(f):
            raise ValueError(f"Setting {key} cannot be NaN or inf")
        if spec.low is not None:
            f = max(spec.low, f)
        if spec.high is not None:
            f = min(spec.high, f)
        return f
    if spec.kind is str:
        s = "" if value is None else str(value)
        if spec.choices and s not in spec.choices:
            raise ValueError(f"Value '{s}' for setting '{key}' is not one of {spec.choices}")
        return s
    raise TypeError(f"Unsupported setting spec kind {spec.kind}")
