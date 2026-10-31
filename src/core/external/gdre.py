"""Optional bridge to GDRE Tools (https://github.com/GDRETools/gdsdecomp, MIT) for compiled GDScript.

Exported games usually ship scripts as ``.gdc`` bytecode, which this project cannot read itself.
When the user points the app at a GDRE Tools executable, it is run headless in "recover scripts only"
mode against the game package and the decompiled ``.gd`` sources are fed to the script harvester.
GDRE is never bundled or downloaded by the application.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from src.utils.logger import logger

DEFAULT_TIMEOUT_SECONDS = 900
_EXECUTABLE_NAMES = ("gdre_tools", "gdre_tools.exe", "gdre_tools.x86_64")
ENV_VAR = "GDRE_TOOLS"


class GdreError(RuntimeError):
    """GDRE Tools failed or produced no usable output."""


def find_gdre(configured: str = "") -> Path | None:
    """Resolves the GDRE executable: explicit setting, ``GDRE_TOOLS`` env var, then ``PATH``."""
    candidates: list[str] = [configured.strip(), os.environ.get(ENV_VAR, "").strip()]
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw)
        if path.is_dir():
            path = next((path / name for name in _EXECUTABLE_NAMES if (path / name).is_file()), path)
        if path.is_file():
            return path.resolve()
    for name in _EXECUTABLE_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    return None


class GdreBackend:
    """Runs GDRE Tools headless to decompile the scripts of a PCK or executable."""

    def __init__(self, executable: str | Path, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.executable = Path(executable)
        self.timeout = timeout

    def decompile_scripts(self, pack_path: str | Path, key: str = "") -> dict[str, str]:
        """Returns ``res://`` path -> decompiled GDScript source for every script in the package."""
        out_dir = Path(tempfile.mkdtemp(prefix="gdre_scripts_"))
        try:
            args = [
                str(self.executable), "--headless", f"--recover={Path(pack_path)}", "--scripts-only",
                f"--output={out_dir}",
            ]
            if key.strip():
                args.append(f"--key={key.strip()}")
            self._run(args, out_dir)
            return self.collect_scripts(out_dir)
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    def _run(self, args: list[str], out_dir: Path) -> None:
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=self.timeout, check=False, cwd=out_dir,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise GdreError(f"GDRE Tools timed out after {self.timeout}s.") from exc
        except OSError as exc:
            raise GdreError(f"Could not start GDRE Tools: {exc}") from exc
        if result.returncode != 0:
            tail = (result.stdout + result.stderr).strip().splitlines()[-3:]
            raise GdreError(f"GDRE Tools exited with {result.returncode}: {' | '.join(tail)}")

    @staticmethod
    def collect_scripts(out_dir: Path) -> dict[str, str]:
        """Reads decompiled ``.gd`` files below ``out_dir`` (GDRE's own ``.autoconverted`` copies are skipped)."""
        scripts: dict[str, str] = {}
        for path in sorted(out_dir.rglob("*.gd")):
            relative = path.relative_to(out_dir)
            if ".autoconverted" in relative.parts or ".godot" in relative.parts:
                continue
            try:
                scripts["res://" + relative.as_posix()] = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError as exc:
                logger.warning(f"Could not read decompiled script {relative}: {exc}")
        if not scripts:
            raise GdreError("GDRE Tools produced no GDScript files (unsupported bytecode version?).")
        return scripts
