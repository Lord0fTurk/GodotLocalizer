"""Locating and launching the selected game's executable (Windows .exe, Linux ELF binary or .sh launcher)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Godot's Linux/BSD export names: Game.x86_64, Game.arm64, Game (no suffix) or a Game.sh launcher.
_LINUX_SUFFIXES = (".x86_64", ".x86_32", ".arm64", ".arm32", ".sh", ".bin", "")


def _is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def _is_launcher(path: Path) -> bool:
    """A file the OS can start as a game: .exe on Windows, an ELF binary or .sh script elsewhere."""
    if not path.is_file():
        return False
    suffix = path.suffix.lower()
    if suffix == ".exe":
        return True
    if suffix not in _LINUX_SUFFIXES:
        return False
    return path.suffix.lower() == ".sh" or _is_elf(path)


def find_game_executable(game_path: str) -> Path | None:
    """The file to start: the selected one if it is a launcher, else a sibling (same name as the .pck first)."""
    target = Path(game_path)
    if _is_launcher(target):
        return target
    parent = target.parent if target.is_file() else target
    try:
        siblings = sorted(p for p in parent.iterdir() if _is_launcher(p))
    except OSError:
        return None
    same_name = [p for p in siblings if p.stem == target.stem]
    return (same_name or siblings or [None])[0]


def has_game_executable(game_path: str) -> bool:
    """True when the selected file is a launcher or sits next to one."""
    return find_game_executable(game_path) is not None


def launch_game(game_path: str) -> bool:
    """Starts the game; returns False when no launcher was found. Raises OSError if it cannot start."""
    exe = find_game_executable(game_path)
    if exe is None:
        return False
    if os.name != "nt" and not os.access(exe, os.X_OK):
        raise PermissionError(f"{exe.name} is not executable (run: chmod +x \"{exe}\")")
    subprocess.Popen([str(exe)], cwd=str(exe.parent))
    return True
