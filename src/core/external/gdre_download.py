"""Consent-based installer for GDRE Tools (https://github.com/GDRETools/gdsdecomp, MIT license).

Nothing here runs on its own: the UI first shows the user the release (tag, file name, size, source, checksum)
and only after an explicit confirmation is the archive downloaded. The download comes solely from the official
GitHub repository, must match the SHA-256 digest GitHub publishes for the asset, and is unpacked into the
application's own data folder (never into a game folder).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from src.core.external.gdre import find_gdre

REPO = "GDRETools/gdsdecomp"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
LICENSE_NAME = "MIT"
_ALLOWED_HOST_SUFFIXES = ("github.com", "githubusercontent.com")
_CHUNK = 256 * 1024
_MAX_ARCHIVE_BYTES = 400 * 1024 * 1024

ProgressCallback = Callable[[float], None]
Opener = Callable[..., Any]


class GdreDownloadError(RuntimeError):
    """Release lookup, download, verification or unpacking failed."""


@dataclass(frozen=True, slots=True)
class GdreRelease:
    """What the user is asked to approve."""

    tag: str
    asset_name: str
    size: int
    url: str
    sha256: str
    license: str = LICENSE_NAME

    @property
    def page(self) -> str:
        return f"https://github.com/{REPO}/releases/tag/{self.tag}"


def platform_key() -> str:
    """Asset-name suffix of the running platform (``windows``, ``linux`` or ``macos``)."""
    if sys.platform.startswith("win"):
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def _check_host(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(host == s or host.endswith("." + s) for s in _ALLOWED_HOST_SUFFIXES):
        raise GdreDownloadError(f"Refusing to use a non-GitHub address: {url}")


def fetch_latest_release(platform: str | None = None, opener: Opener = urlopen) -> GdreRelease:
    """Looks up the newest stable GDRE Tools release for this platform (read-only network request)."""
    _check_host(API_LATEST)
    request = Request(API_LATEST, headers={"Accept": "application/vnd.github+json", "User-Agent": "GodotLocalizer"})
    try:
        with opener(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise GdreDownloadError(f"Could not read the GDRE Tools release list: {exc}") from exc
    if data.get("prerelease") or data.get("draft"):
        raise GdreDownloadError("The latest GDRE Tools release is not a stable release.")

    suffix = f"-{platform or platform_key()}.zip"
    for asset in data.get("assets", []):
        name = str(asset.get("name", ""))
        if not name.endswith(suffix):
            continue
        digest = str(asset.get("digest", ""))
        if not digest.startswith("sha256:") or len(digest) != 7 + 64:
            raise GdreDownloadError("GitHub published no SHA-256 digest for this asset; refusing to download.")
        url = str(asset.get("browser_download_url", ""))
        _check_host(url)
        return GdreRelease(str(data.get("tag_name", "")), name, int(asset.get("size", 0)), url, digest[7:].lower())
    raise GdreDownloadError(f"No GDRE Tools build for '{platform or platform_key()}' in the latest release.")


def _download(release: GdreRelease, target: Path, progress: ProgressCallback | None, opener: Opener) -> None:
    request = Request(release.url, headers={"User-Agent": "GodotLocalizer"})
    hasher = hashlib.sha256()
    received = 0
    try:
        with opener(request, timeout=60) as response, open(target, "wb") as out:
            _check_host(response.geturl())  # redirects must stay on GitHub's hosts
            while chunk := response.read(_CHUNK):
                received += len(chunk)
                if received > min(_MAX_ARCHIVE_BYTES, max(release.size, 1) * 2):
                    raise GdreDownloadError("The download is larger than announced; aborting.")
                hasher.update(chunk)
                out.write(chunk)
                if progress and release.size:
                    progress(min(received / release.size, 1.0))
    except OSError as exc:
        raise GdreDownloadError(f"Download failed: {exc}") from exc
    if release.size and received != release.size:
        raise GdreDownloadError(f"Downloaded {received} bytes but {release.size} were announced.")
    if hasher.hexdigest() != release.sha256:
        raise GdreDownloadError("SHA-256 mismatch: the file is not the one GitHub published.")


def _safe_extract(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(root):
                raise GdreDownloadError(f"Unsafe path in archive: {member.filename}")
        zf.extractall(destination)


def download_and_install(
    release: GdreRelease,
    install_root: Path,
    progress: ProgressCallback | None = None,
    opener: Opener = urlopen,
) -> Path:
    """Downloads, verifies and unpacks the release into ``install_root/<tag>``; returns the executable."""
    _check_host(release.url)
    destination = install_root / release.tag
    with tempfile.TemporaryDirectory(prefix="gdre_dl_") as tmp:
        archive = Path(tmp) / release.asset_name
        _download(release, archive, progress, opener)
        staging = Path(tmp) / "unpacked"
        try:
            _safe_extract(archive, staging)
        except zipfile.BadZipFile as exc:
            raise GdreDownloadError("The downloaded archive is corrupt.") from exc
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(destination))
    executable = find_gdre(str(destination))
    if executable is None:
        raise GdreDownloadError("The archive did not contain a gdre_tools executable.")
    if sys.platform != "win32":
        executable.chmod(executable.stat().st_mode | 0o111)
    return executable
