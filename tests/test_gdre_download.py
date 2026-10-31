"""GDRE Tools installer: release lookup, host allow-list, checksum and unpacking safety (no network used)."""

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.core.external.gdre_download import (
    GdreDownloadError,
    GdreRelease,
    download_and_install,
    fetch_latest_release,
)


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, url: str = "https://github.com/x") -> None:
        super().__init__(payload)
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def release_json(archive: bytes, name: str = "GDRE_tools-v9.9.9-windows.zip", **overrides: object) -> bytes:
    asset = {
        "name": name,
        "size": len(archive),
        "browser_download_url": f"https://github.com/GDRETools/gdsdecomp/releases/download/v9.9.9/{name}",
        "digest": "sha256:" + hashlib.sha256(archive).hexdigest(),
    }
    asset.update(overrides)
    return json.dumps({"tag_name": "v9.9.9", "prerelease": False, "assets": [asset]}).encode()


class TestRelease(unittest.TestCase):
    def test_picks_the_platform_asset_and_digest(self) -> None:
        archive = make_zip({"gdre_tools.exe": b"MZ"})
        release = fetch_latest_release("windows", opener=lambda *a, **k: FakeResponse(release_json(archive)))
        self.assertEqual((release.tag, release.asset_name, release.size), ("v9.9.9", "GDRE_tools-v9.9.9-windows.zip", len(archive)))
        self.assertEqual(release.sha256, hashlib.sha256(archive).hexdigest())

    def test_refuses_without_digest_or_platform_or_foreign_host(self) -> None:
        archive = make_zip({"gdre_tools.exe": b"MZ"})
        for payload, platform in (
            (release_json(archive, digest=""), "windows"),
            (release_json(archive), "linux"),
            (release_json(archive, browser_download_url="https://evil.example/gdre.zip"), "windows"),
        ):
            with self.assertRaises(GdreDownloadError):
                fetch_latest_release(platform, opener=lambda *a, p=payload, **k: FakeResponse(p))


class TestInstall(unittest.TestCase):
    def install(self, archive: bytes, release: GdreRelease | None = None, url: str = "https://github.com/a") -> Path:
        release = release or GdreRelease(
            "v9.9.9", "g.zip", len(archive), "https://github.com/GDRETools/gdsdecomp/g.zip", hashlib.sha256(archive).hexdigest()
        )
        progress: list[float] = []
        with tempfile.TemporaryDirectory() as tmp:
            exe = download_and_install(
                release, Path(tmp) / "gdre", progress.append, opener=lambda *a, **k: FakeResponse(archive, url)
            )
            self.assertTrue(exe.is_file())
            self.assertEqual(progress[-1], 1.0)
            return exe.resolve().relative_to(Path(tmp).resolve())  # CI temp dirs use 8.3 short names

    def test_installs_and_finds_the_executable(self) -> None:
        exe = self.install(make_zip({"gdre_tools.exe": b"MZ", "gdre_tools.pck": b"x"}))
        self.assertEqual(exe.parts[-3:], ("gdre", "v9.9.9", exe.name))

    def test_checksum_mismatch_is_rejected(self) -> None:
        archive = make_zip({"gdre_tools.exe": b"MZ"})
        bad = GdreRelease("v9.9.9", "g.zip", len(archive), "https://github.com/GDRETools/gdsdecomp/g.zip", "0" * 64)
        with self.assertRaisesRegex(GdreDownloadError, "SHA-256"):
            self.install(archive, bad)

    def test_redirect_to_foreign_host_is_rejected(self) -> None:
        with self.assertRaises(GdreDownloadError):
            self.install(make_zip({"gdre_tools.exe": b"MZ"}), url="https://evil.example/x.zip")

    def test_zip_slip_is_rejected(self) -> None:
        with self.assertRaisesRegex(GdreDownloadError, "Unsafe"):
            self.install(make_zip({"../escape.txt": b"x", "gdre_tools.exe": b"MZ"}))

    def test_archive_without_executable_is_rejected(self) -> None:
        with self.assertRaisesRegex(GdreDownloadError, "executable"):
            self.install(make_zip({"readme.txt": b"x"}))

    def test_oversized_download_is_aborted(self) -> None:
        archive = make_zip({"gdre_tools.exe": b"MZ" * 1000})
        short = GdreRelease("v9.9.9", "g.zip", 10, "https://github.com/GDRETools/gdsdecomp/g.zip", hashlib.sha256(archive).hexdigest())
        with self.assertRaisesRegex(GdreDownloadError, "larger"):
            self.install(archive, short)


if __name__ == "__main__":
    unittest.main()
