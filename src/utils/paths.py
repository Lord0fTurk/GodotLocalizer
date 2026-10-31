"""Application and user-data path resolution for GodotLocalizer."""

from __future__ import annotations

import functools
import os
import platform
import sys
from pathlib import Path

APP_NAME = "GodotLocalizer"
DATA_DIR_ENV = "GODOTLOCALIZER_DATA_DIR"
PORTABLE_MARKER = ".portable"


def get_app_dir() -> Path:
    """Directory of the executable (frozen) or the project root (source checkout)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def get_project_root() -> Path:
    """Returns the absolute path to the project root directory."""
    return get_app_dir()


def get_base_dir() -> Path:
    """Backward-compatible alias of ``get_app_dir``."""
    return get_app_dir()


def resource_path(relative_path: str | Path) -> Path:
    """Resolve an asset or resource path, checking _MEIPASS if bundled."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / relative_path
        if bundled.exists():
            return bundled
    return get_app_dir() / relative_path


def get_system_data_dir() -> Path:
    """OS-standard per-user data directory for the application."""
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME


def is_portable() -> bool:
    """True when user data is kept beside the application."""
    app_dir = get_app_dir()
    if (app_dir / PORTABLE_MARKER).exists():
        return True
    return (app_dir / "config.json").is_file() and os.access(app_dir, os.W_OK)


@functools.cache
def get_data_dir() -> Path:
    """Directory holding all user data (resolved once per process, then created)."""
    override = os.environ.get(DATA_DIR_ENV)
    data_dir = Path(override) if override else (get_app_dir() if is_portable() else get_system_data_dir())
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_config_path() -> Path:
    return get_data_dir() / "config.json"


def get_glossary_path() -> Path:
    return get_data_dir() / "glossary.json"


def get_logs_dir() -> Path:
    """Ensure and return the logs directory."""
    return _ensure(get_data_dir() / "logs")


def get_default_logs_dir() -> Path:
    return get_logs_dir()


def get_cache_dir() -> Path:
    """Ensure and return the cache directory."""
    return _ensure(get_data_dir() / "cache")


def get_patches_dir() -> Path:
    """Ensure and return the default output directory for exported patches."""
    return _ensure(get_data_dir() / "Patches")


# --- Godot Virtual Filesystem Path Utilities ---

def sanitize_godot_path(res_path: str) -> str:
    """Normalizes Godot virtual resource path to a standard forward-slash format."""
    clean = res_path.replace("\\", "/").strip()
    if not clean.startswith("res://"):
        clean = "res://" + clean.lstrip("/")
    return clean


def res_to_relative_path(res_path: str) -> str:
    """Converts a Godot 'res://' path to a relative local path."""
    clean = sanitize_godot_path(res_path)
    return clean[len("res://"):]
