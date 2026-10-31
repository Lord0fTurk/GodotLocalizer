"""Small stateless helpers shared by the QML-facing backends."""

from __future__ import annotations

import os
import urllib.parse

from PyQt6.QtCore import QUrl

from src.core.languages import Language


def language_entry(language: Language) -> dict[str, str]:
    """QML model row for language combo boxes."""
    label = language.native if language.native == language.english else f"{language.native} — {language.english}"
    return {"code": language.code, "name": label, "english": language.english, "native": language.native}


def local_path(path_or_url: str) -> str:
    """Converts a QML file URL or raw path to clean local filesystem path."""
    raw = str(path_or_url).strip()
    if raw.lower().startswith("file:"):
        local = QUrl(raw).toLocalFile()
        if local:
            return local
        clean = urllib.parse.unquote(raw)
        for prefix in ("file:///", "file://"):
            if clean.lower().startswith(prefix):
                rest = clean[len(prefix):]
                return rest if os.name == "nt" else "/" + rest.lstrip("/")
    return raw
