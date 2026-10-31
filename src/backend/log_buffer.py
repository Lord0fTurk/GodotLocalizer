"""Thread-safe, size-limited log list backing the console view."""

from __future__ import annotations

import threading
import time


class LogBuffer:
    """Keeps the most recent log rows (level, message, timestamp) for the UI."""

    def __init__(self, limit: int = 500) -> None:
        self._limit = limit
        self._rows: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def add(self, level: str, message: str) -> None:
        row = {"level": level, "message": message, "ts": time.strftime("%H:%M:%S")}
        with self._lock:
            self._rows.append(row)
            if len(self._rows) > self._limit:
                self._rows.pop(0)

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()

    def snapshot(self) -> list[dict[str, str]]:
        with self._lock:
            return list(self._rows)

    def as_text(self) -> str:
        return "\n".join(f"[{r['ts']}] [{r['level'].upper()}] {r['message']}" for r in self.snapshot())
