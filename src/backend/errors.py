"""Maps exceptions to localized messages so the UI never shows a raw English traceback line."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.core.pck.reader import CustomPckSignatureError
from src.core.pipeline.orchestrator import NoGamePackageError


@dataclass(frozen=True)
class ErrorInfo:
    """An i18n key with its parameters; ``blocking`` errors deserve a dialog, not just a toast."""

    key: str
    params: dict[str, Any] = field(default_factory=dict)
    blocking: bool = False


def describe_error(exc: BaseException) -> ErrorInfo:
    """The message to show for an exception (unknown ones fall back to the generic localized prefix)."""
    if isinstance(exc, CustomPckSignatureError):
        return ErrorInfo("errors.custom_signature", {"signature": exc.signature.decode("latin-1"), "file": exc.file_name}, True)
    if isinstance(exc, NoGamePackageError):
        return ErrorInfo("errors.no_game_package", {"folder": exc.folder}, True)
    return ErrorInfo("dashboard.status_error", {"error": str(exc)})
