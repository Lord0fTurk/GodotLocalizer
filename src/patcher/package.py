"""Shareable patch packages and patch-status tracking.

A finished localization is a handful of small files next to the game executable. This module

* records a small sidecar (``godotlocalizer_patch.json``) when a patch is deployed, so the app can later tell
  whether a game folder is patched, in which language, and whether the game was updated afterwards;
* exports a self-contained ``.zip`` that someone without this app can install (install/uninstall scripts for
  Windows and Linux/Steam Deck, plus a README) and that this app can install safely with
  :func:`install_package` (which merges ``override.cfg`` instead of overwriting it).

The package never contains game files, only the patch.
"""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.langpack import installed_pack
from src.patcher.overlay_patcher import OverlayPatcher
from src.patcher.override_cfg import AUTOLOAD_KEY, add_autoload
from src.version import __version__

SIDECAR_NAME = OverlayPatcher.SIDECAR_NAME
_FINGERPRINT_SLICE = 1024 * 1024


def fingerprint(pack_path: str | Path) -> dict[str, Any]:
    """Cheap identity of a game package: name, size and a hash of its first and last megabyte."""
    path = Path(pack_path)
    size = path.stat().st_size
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        digest.update(stream.read(_FINGERPRINT_SLICE))
        if size > _FINGERPRINT_SLICE:
            stream.seek(max(size - _FINGERPRINT_SLICE, _FINGERPRINT_SLICE))
            digest.update(stream.read(_FINGERPRINT_SLICE))
    return {"name": path.name, "size": size, "sha256": digest.hexdigest()}


def write_sidecar(
    game_dir: Path, locale: str, is_godot4: bool, engine_version: tuple[int, int, int], base_pack: Path | None
) -> None:
    """Records what was deployed into ``game_dir`` (called after a successful overlay deployment)."""
    info: dict[str, Any] = {
        "tool": "GodotLocalizer",
        "tool_version": __version__,
        "locale": locale,
        "godot4": is_godot4,
        "engine_version": list(engine_version),
        "patch_pck": OverlayPatcher.DEFAULT_PATCH_PCK,
        "bootstrap": OverlayPatcher.BOOTSTRAP_SCRIPT_NAME,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if base_pack is not None and base_pack.is_file():
        info["base_pack"] = fingerprint(base_pack)
    (game_dir / SIDECAR_NAME).write_text(json.dumps(info, indent=2), encoding="utf-8")


def read_sidecar(game_dir: str | Path) -> dict[str, Any] | None:
    try:
        return json.loads((Path(game_dir) / SIDECAR_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@dataclass(slots=True)
class PatchStatus:
    """What the UI shows about a game folder."""

    patched: bool = False
    locale: str = ""
    created: str = ""
    game_updated: bool = False  # the game package changed after the patch was made
    warnings: list[str] = field(default_factory=list)


def patch_status(game_dir: str | Path, game_pack: str | Path | None = None) -> PatchStatus:
    """Is ``game_dir`` patched, in which language, and is the patch still matching the game package?"""
    folder = Path(game_dir)
    if not (folder / OverlayPatcher.DEFAULT_PATCH_PCK).is_file():
        pack = installed_pack(folder)  # games with their own language packs get a pack instead of an overlay
        return PatchStatus(patched=True, locale=str(pack.get("locale", ""))) if pack else PatchStatus()
    status = PatchStatus(patched=True)
    info = read_sidecar(folder)
    if info is None:
        return status
    status.locale, status.created = str(info.get("locale", "")), str(info.get("created", ""))
    base = info.get("base_pack")
    pack = Path(game_pack) if game_pack else (folder / str(base["name"]) if isinstance(base, dict) else None)
    if isinstance(base, dict) and pack is not None and pack.is_file():
        status.game_updated = fingerprint(pack) != base
    return status


# --- export ---------------------------------------------------------------------------------------

_INSTALL_BAT = """@echo off
rem GodotLocalizer patch installer / Yama kurucu
cd /d "%~dp0"
if not exist "{pck}" (echo Run this from the game folder after extracting the zip there. & pause & exit /b 1)
if exist override.cfg (
  findstr /c:"{key}" override.cfg >nul && (echo Already installed. & pause & exit /b 0)
  echo.>> override.cfg
  type override.cfg.godotlocalizer >> override.cfg
) else (
  copy /y override.cfg.godotlocalizer override.cfg >nul
)
echo Installed. Start the game normally. / Kuruldu, oyunu normal sekilde baslatin.
pause
"""

_UNINSTALL_BAT = """@echo off
rem GodotLocalizer patch remover / Yama kaldirici
cd /d "%~dp0"
del /q "{pck}" "{bootstrap}" "{sidecar}" 2>nul
if exist override.cfg (
  findstr /v /c:"{key}" override.cfg > override.cfg.tmp
  move /y override.cfg.tmp override.cfg >nul
)
echo Removed. / Kaldirildi.
pause
"""

_INSTALL_SH = """#!/bin/sh
# GodotLocalizer patch installer / Yama kurucu
cd "$(dirname "$0")" || exit 1
[ -f "{pck}" ] || {{ echo "Run this from the game folder after extracting the zip there."; exit 1; }}
if [ -f override.cfg ]; then
  grep -q "{key}" override.cfg && {{ echo "Already installed."; exit 0; }}
  printf '\\n' >> override.cfg
  cat override.cfg.godotlocalizer >> override.cfg
else
  cp override.cfg.godotlocalizer override.cfg
fi
echo "Installed. Start the game normally. / Kuruldu."
"""

_UNINSTALL_SH = """#!/bin/sh
# GodotLocalizer patch remover / Yama kaldirici
cd "$(dirname "$0")" || exit 1
rm -f "{pck}" "{bootstrap}" "{sidecar}"
if [ -f override.cfg ]; then
  grep -v "{key}" override.cfg > override.cfg.tmp && mv override.cfg.tmp override.cfg
fi
echo "Removed. / Kaldirildi."
"""

_README = """GodotLocalizer patch - {game} ({locale})
================================================

ENGLISH
1. Extract every file of this zip into the folder that contains the game executable ("{game}").
2. Run install.bat (Windows) or install.sh (Linux / Steam Deck). It adds the patch to override.cfg without
   touching your game files. If the game has no override.cfg, extracting alone is not enough: run the installer.
3. Start the game normally.
To remove: run uninstall.bat / uninstall.sh. The game itself is never modified.
Made for: {base}. If the game was updated, the patch may need to be rebuilt by its author.

TURKCE
1. Bu zip'teki tum dosyalari oyunun calistirilabilir dosyasinin bulundugu klasore cikarin.
2. install.bat (Windows) veya install.sh (Linux / Steam Deck) calistirin. Oyun dosyalarina dokunmadan yamayi override.cfg'ye ekler.
3. Oyunu normal sekilde baslatin.
Kaldirmak icin: uninstall.bat / uninstall.sh. Oyunun kendisi hicbir zaman degistirilmez.
Hedef surum: {base}. Oyun guncellenirse yamanin yeniden uretilmesi gerekebilir.

Generated by GodotLocalizer {version}.
"""


def export_package(game_dir: str | Path, out_zip: str | Path, game_name: str = "") -> Path:
    """Builds a distributable zip from the patch currently deployed in ``game_dir``."""
    folder = Path(game_dir)
    pck, script = folder / OverlayPatcher.DEFAULT_PATCH_PCK, folder / OverlayPatcher.BOOTSTRAP_SCRIPT_NAME
    if not pck.is_file() or not script.is_file():
        raise FileNotFoundError("This game folder has no GodotLocalizer patch to export; translate it first.")
    info = read_sidecar(folder) or {}
    base = info.get("base_pack", {}) if isinstance(info.get("base_pack"), dict) else {}
    values = {
        "pck": pck.name, "bootstrap": script.name, "sidecar": SIDECAR_NAME, "key": AUTOLOAD_KEY,
        "game": game_name or str(base.get("name", "game")), "locale": info.get("locale", ""),
        "base": f"{base.get('name', '?')} ({base.get('size', '?')} bytes)", "version": __version__,
    }
    override = add_autoload("", f"res://{script.name}", prepend=bool(info.get("godot4", True)))

    out = Path(out_zip)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(pck, pck.name)
        zf.write(script, script.name)
        if (folder / SIDECAR_NAME).is_file():
            zf.write(folder / SIDECAR_NAME, SIDECAR_NAME)
        zf.writestr("override.cfg.godotlocalizer", override)
        for name, template in (("install.bat", _INSTALL_BAT), ("uninstall.bat", _UNINSTALL_BAT)):
            zf.writestr(name, template.format(**values).replace("\n", "\r\n"))
        for name, template in (("install.sh", _INSTALL_SH), ("uninstall.sh", _UNINSTALL_SH)):
            entry = zipfile.ZipInfo(name)
            entry.external_attr = 0o755 << 16
            zf.writestr(entry, template.format(**values))
        zf.writestr("README.txt", _README.format(**values))
    return out


# --- install --------------------------------------------------------------------------------------


@dataclass(slots=True)
class InstallResult:
    locale: str = ""
    warnings: list[str] = field(default_factory=list)


def _safe_members(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members = {m.filename: m for m in zf.infolist() if not m.is_dir()}
    for name in members:
        if name.startswith(("/", "\\")) or ".." in Path(name).parts:
            raise ValueError(f"Unsafe path in patch package: {name}")
    return members


def install_package(zip_path: str | Path, game_dir: str | Path, game_pack: str | Path | None = None) -> InstallResult:
    """Installs a package made by :func:`export_package` into ``game_dir``, merging ``override.cfg``."""
    folder = Path(game_dir)
    pck_name, script_name = OverlayPatcher.DEFAULT_PATCH_PCK, OverlayPatcher.BOOTSTRAP_SCRIPT_NAME
    with zipfile.ZipFile(zip_path) as zf:
        members = _safe_members(zf)
        if pck_name not in members or script_name not in members:
            raise ValueError("This zip is not a GodotLocalizer patch package.")
        info = json.loads(zf.read(SIDECAR_NAME)) if SIDECAR_NAME in members else {}
        result = InstallResult(locale=str(info.get("locale", "")))
        base = info.get("base_pack")
        pack = Path(game_pack) if game_pack else (folder / str(base["name"]) if isinstance(base, dict) else None)
        if isinstance(base, dict) and pack is not None:
            if not pack.is_file():
                result.warnings.append(f"The game package '{base['name']}' was not found in this folder.")
            elif fingerprint(pack) != base:
                result.warnings.append("The game differs from the version this patch was made for; some text may stay untranslated.")
        for name in (pck_name, script_name, SIDECAR_NAME):
            if name in members:
                (folder / name).write_bytes(zf.read(name))

    cfg = folder / "override.cfg"
    existing = cfg.read_text(encoding="utf-8-sig") if cfg.is_file() else ""
    merged = add_autoload(existing, f"res://{script_name}", prepend=bool(info.get("godot4", True)))
    cfg.write_bytes(merged.encode("utf-8"))
    return result


def uninstall(game_dir: str | Path) -> int:
    """Removes the patch (same as the Clean Revert in the toolbox); returns the number of removed items."""
    return OverlayPatcher(game_dir).remove_patch()


__all__ = [
    "SIDECAR_NAME", "InstallResult", "PatchStatus", "export_package", "fingerprint", "install_package",
    "patch_status", "read_sidecar", "uninstall", "write_sidecar",
]
