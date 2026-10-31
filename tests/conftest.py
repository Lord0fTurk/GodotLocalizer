"""Test-suite wide settings."""

import tempfile

import pytest


class _TolerantTemporaryDirectory(tempfile.TemporaryDirectory):
    """On Windows a scanner or indexer can briefly hold a freshly written file; do not fail a test over cleanup."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("ignore_cleanup_errors", True)
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _tolerant_temp_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tempfile, "TemporaryDirectory", _TolerantTemporaryDirectory)
