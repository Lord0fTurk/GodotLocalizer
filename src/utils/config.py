"""Persistent JSON configuration manager for GodotLocalizer."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Protocol

from src.utils.file_ops import safe_write
from src.utils.paths import get_config_path, is_portable

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "target_language": "tr",
    "source_language": "auto",
    "rtl_static_shaping": True,
    "bilingual_mode": False,
    "bilingual_template": "{target}\\n({source})",  # "\n" typed = line break (one-line settings field)
    "patch_strategy": "overlay",  # 'overlay' (Method A) | 'repack' (Method B)
    "json_text_keys": "",  # extra JSON key names (comma separated) whose string values are game text
    "gdre_path": "",  # optional path to GDRE Tools (gdre_tools.exe) for compiled .gdc scripts
    "pck_encryption_key": "",  # 64-char hex AES-256 key for encrypted PCKs
    "custom_font_path": "",
    "font_preset": "default_arial",
    "ui_language": "tr",
    # Session
    "last_game_path": "",
    "window_width": 0,
    "window_height": 0,
    "window_maximized": False,
    "theme_mode": "obsidian",
    "accent_preset": "emerald",
    "engine": "google",
    "batch_size": 100,
    # Network
    "rate_limit_delay": 0.15,
    "max_concurrency": 4,
    "request_timeout": 30,
    "max_chars_per_request": 5000,
    "use_multi_endpoint": True,
    "lingva_fallback": True,
    "aggressive_retry": False,
    "proxy_enabled": False,
    "proxy_list": "",
    # AI generation
    "ai_concurrency": 2,
    "ai_temperature": 0.3,
    "ai_max_tokens": 4096,
    "ai_timeout": 120,
    "ai_retries": 2,
    "ai_request_delay": 0.5,
    "ai_batch_size": 15,
    "ai_custom_prompt": "",
    "openai_key": "",
    "openai_model": "gpt-4o-mini",
    "openai_base_url": "",
    "gemini_key": "",
    "gemini_model": "gemini-2.5-flash",
    "gemini_safety": "BLOCK_NONE",
    "deepseek_key": "",
    "deepseek_model": "deepseek-chat",
    "local_llm_url": "http://localhost:11434",
    "local_llm_model": "qwen2.5:latest",
    "local_llm_timeout": 300,
    # Other engines
    "deepl_key": "",
    "deepl_formality": "default",
    "libretranslate_url": "http://localhost:5000",
    "libretranslate_key": "",
    "show_debug_engines": False,
    # Translation memory, cache & terminology
    "use_cache": True,
    "tm_enabled": False,
    "tm_files": "",
    "tm_fuzzy": False,
    "tm_fuzzy_threshold": 0.9,
    "protected_terms": "",
}


KEYRING_SERVICE = "GodotLocalizer"
DISABLE_KEYRING_ENV = "GODOTLOCALIZER_NO_KEYRING"
# Credentials that live in the operating system's credential store instead of config.json when one is available.
SECRET_KEYS = frozenset({
    "openai_key", "gemini_key", "deepseek_key", "deepl_key", "libretranslate_key", "pck_encryption_key",
})


class SecretVault(Protocol):
    """The subset of the ``keyring`` API the configuration needs."""

    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


def default_vault() -> SecretVault | None:
    """The OS credential store via the optional ``keyring`` package, or None (portable mode, no backend)."""
    if os.environ.get(DISABLE_KEYRING_ENV) == "1" or is_portable():
        return None
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return None
    return None if isinstance(keyring.get_keyring(), fail.Keyring) else keyring  # type: ignore[return-value]


_AUTO: Any = object()


class ConfigManager:
    """Manages application settings stored in a JSON configuration file.

    API keys and the PCK encryption key are kept in the OS credential store when ``keyring`` is installed
    (and written as empty strings to the JSON file); plaintext values found in an older file are migrated.
    Without a credential store everything stays in the file, as before.
    """

    def __init__(self, config_path: Path | str | None = None, vault: SecretVault | None = _AUTO) -> None:
        self.config_path = Path(config_path) if config_path else get_config_path()
        self._vault: SecretVault | None = default_vault() if vault is _AUTO else vault
        self._secrets: dict[str, str] = {}
        self._config: dict[str, Any] = {}
        self.load()

    # --- secrets ---------------------------------------------------------------------------------

    def _vault_read(self, key: str) -> str:
        if key not in self._secrets and self._vault is not None:
            try:
                self._secrets[key] = self._vault.get_password(KEYRING_SERVICE, key) or ""
            except Exception as exc:
                logger.warning("Credential store read failed for %s: %s", key, exc)
                self._secrets[key] = ""
        return self._secrets.get(key, "")

    def _vault_write(self, key: str, value: str) -> bool:
        """Stores a secret; False when there is no usable credential store (the caller keeps it in the file)."""
        if self._vault is None:
            return False
        try:
            if value:
                self._vault.set_password(KEYRING_SERVICE, key, value)
            else:
                try:
                    self._vault.delete_password(KEYRING_SERVICE, key)
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Credential store write failed for %s (keeping it in the config file): %s", key, exc)
            return False
        self._secrets[key] = value
        return True

    def _migrate_plaintext_secrets(self) -> None:
        moved = False
        for key in SECRET_KEYS:
            value = str(self._config.get(key) or "")
            if value and self._vault_write(key, value):
                self._config[key] = ""
                moved = True
        if moved:
            self.save()

    def load(self) -> dict[str, Any]:
        """Load configuration from disk, falling back to default values."""
        self._config = dict(DEFAULT_CONFIG)
        if self.config_path.is_file():
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    user_config = json.load(f)
                if isinstance(user_config, dict):
                    self._config.update(user_config)
            except Exception as e:
                logger.warning("Could not read configuration file: %s", e)
        self._migrate_plaintext_secrets()
        return self._config

    def save(self) -> bool:
        """Write current configuration to disk safely."""
        try:
            content = json.dumps(self._config, indent=2, ensure_ascii=False)
            safe_write(self.config_path, content, make_backup=False)
            return True
        except Exception as e:
            logger.error("Failed to save configuration: %s", e)
            return False

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a configuration value (secrets come from the credential store when they are kept there)."""
        if key in SECRET_KEYS:
            secret = self._vault_read(key)
            if secret:
                return secret
        return self._config.get(key, default if default is not None else DEFAULT_CONFIG.get(key))

    def set(self, key: str, value: Any) -> None:
        """Update a configuration value and persist immediately."""
        self._set(key, value)
        self.save()

    def _set(self, key: str, value: Any) -> None:
        if key in SECRET_KEYS and self._vault_write(key, str(value or "")):
            self._config[key] = ""
        else:
            self._config[key] = value

    def update(self, updates: dict[str, Any]) -> None:
        """Update multiple configuration keys and persist to disk."""
        for key, value in updates.items():
            self._set(key, value)
        self.save()

    def to_dict(self) -> dict[str, Any]:
        """Return a copy of the configuration dictionary (including secrets, for the settings UI)."""
        merged = dict(self._config)
        for key in SECRET_KEYS:
            secret = self._vault_read(key)
            if secret:
                merged[key] = secret
        return merged
