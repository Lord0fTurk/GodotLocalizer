"""Standalone PyInstaller build for GodotLocalizer (used locally and by .github/workflows/release.yml)."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("Builder")

APP_NAME = "GodotLocalizer"


def ensure_pyinstaller() -> bool:
    """Ensure PyInstaller is installed in the current environment."""
    try:
        import PyInstaller  # noqa: F401
        return True
    except ImportError:
        logger.info("PyInstaller not found. Installing via pip...")
        res = subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=False)
        return res.returncode == 0


def get_data_args(root_dir: Path) -> list[str]:
    """--add-data arguments (QML views, UI translations, icons) with the platform's path separator."""
    sep = ";" if sys.platform == "win32" else ":"
    args: list[str] = []
    for src, dst in (("src/gui/qml", "src/gui/qml"), ("src/gui/i18n", "src/gui/i18n"), ("icon.ico", "."), ("icon.png", ".")):
        path = root_dir / src
        if path.exists():
            args.extend(["--add-data", f"{path}{sep}{dst}"])
    return args


def get_hidden_imports() -> list[str]:
    """Modules PyInstaller cannot see: QML engine plugins are loaded by name at runtime."""
    modules = (
        "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtQuickControls2",
        "sqlite3", "urllib.request", "zipfile", "xml.etree.ElementTree",
    )
    args: list[str] = []
    for module in modules:
        args.extend(["--hidden-import", module])
    return args


def get_collect_args() -> list[str]:
    """fontTools loads its table classes lazily by tag; the optional keyring backends likewise."""
    return ["--collect-submodules", "fontTools", "--collect-submodules", "src"]


def build_binary(root_dir: Path, onefile: bool = False, console: bool = False) -> int:
    """Run PyInstaller, then the frozen program's own ``--self-check``."""
    cmd = [sys.executable, "-m", "PyInstaller", "--name", APP_NAME, "--noconfirm", "--clean"]
    cmd.append("--onefile" if onefile else "--onedir")
    cmd.append("--console" if console else "--windowed")
    icon = root_dir / "icon.ico"
    if icon.is_file() and sys.platform == "win32":  # .ico is a Windows icon
        cmd.extend(["--icon", str(icon)])
    cmd.extend(get_data_args(root_dir))
    cmd.extend(get_hidden_imports())
    cmd.extend(get_collect_args())
    cmd.append(str(root_dir / "run.py"))

    logger.info("Starting PyInstaller packaging...")
    result = subprocess.run(cmd, cwd=str(root_dir), check=False)
    if result.returncode != 0:
        logger.error("Build failed with returncode %s", result.returncode)
        return result.returncode
    logger.info("Build completed. Output in: %s", root_dir / "dist")
    return verify_binary(root_dir, onefile)


def verify_binary(root_dir: Path, onefile: bool) -> int:
    """Run ``--self-check`` in the frozen app so missing package data fails the build."""
    exe = root_dir / "dist" / (f"{APP_NAME}.exe" if onefile else f"{APP_NAME}/{APP_NAME}.exe")
    if not exe.is_file():
        exe = exe.with_suffix("")  # Linux/macOS builds have no .exe suffix
    result = subprocess.run([str(exe), "--self-check"], cwd=str(root_dir), check=False, timeout=300)
    if result.returncode != 0:
        logger.error("Frozen self-check failed")
    else:
        logger.info("Frozen self-check passed")
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Build the {APP_NAME} standalone executable.")
    parser.add_argument("--onefile", action="store_true", help="Package into a single executable")
    parser.add_argument("--console", action="store_true", help="Keep a console window (debugging)")
    args = parser.parse_args()
    if not ensure_pyinstaller():
        logger.error("Failed to install or verify PyInstaller.")
        return 1
    return build_binary(Path(__file__).resolve().parent, onefile=args.onefile, console=args.console)


if __name__ == "__main__":
    sys.exit(main())
