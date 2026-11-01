"""Command-line interface: the same pipeline as the app, for scripts, servers and batch jobs.

    python -m src.cli inspect  <game>
    python -m src.cli translate <game> --lang tr [--engine google] [--json-keys a,b] [--pck-key HEX]
    python -m src.cli status | export | install | revert ...

Exit codes: 0 success, 1 error, 2 bad usage, 3 nothing to translate, 4 encryption key needed or wrong.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.core.external.gdre import find_gdre, managed_install_root
from src.core.external.gdre_download import download_and_install, fetch_latest_release
from src.core.langpack import find_language_root, remove_packs
from src.core.pck.crypto import PCKEncryptionError
from src.core.pck.reader import PCKReader
from src.core.pipeline.analysis import count_unreadable_scripts, find_json_key_candidates, find_unscanned_text_files
from src.core.pipeline.harvest import parse_json_keys
from src.core.pipeline.orchestrator import GodotTranslationPipeline, NothingToTranslateError
from src.core.progress import ProgressEvent
from src.core.translators.base import TranslationEngine
from src.core.translators.manager import TranslationManager
from src.patcher.overlay_patcher import OverlayPatcher
from src.patcher.package import export_package, install_package, patch_status
from src.utils.config import ConfigManager
from src.version import __version__

COMMANDS = ("inspect", "translate", "status", "export", "install", "revert", "gdre-install")
EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_NOTHING, EXIT_KEY = 0, 1, 2, 3, 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="godotlocalizer", description="Localize exported Godot games.")
    parser.add_argument("--version", action="version", version=f"GodotLocalizer {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def game_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument("game", help="game folder, .pck or .exe")
        p.add_argument("--pck-key", default="", help="64-character hex key of an encrypted game")

    inspect = sub.add_parser("inspect", help="show engine, package and what a translation would need")
    game_arg(inspect)
    inspect.add_argument("--gdre", default="", help="path to gdre_tools (optional)")

    translate = sub.add_parser("translate", help="harvest, translate and patch the game")
    game_arg(translate)
    translate.add_argument("--lang", required=True, help="target language code, e.g. tr, de, pt-BR")
    translate.add_argument("--source", default="auto", help="source language (default: auto)")
    translate.add_argument("--engine", default="google", choices=[e.value for e in TranslationEngine])
    translate.add_argument("--strategy", default="overlay", choices=("overlay", "repack"))
    translate.add_argument("--json-keys", default="", help="extra JSON field names that hold text, comma separated")
    translate.add_argument("--gdre", default="", help="path to gdre_tools (optional)")
    translate.add_argument("--font", default="", help="fallback font (.ttf/.otf) to ship with the patch")
    translate.add_argument("--quiet", action="store_true", help="no progress lines")

    gdre = sub.add_parser("gdre-install", help="download GDRE Tools (reads old compiled scripts); asks before downloading")
    gdre.add_argument("--yes", action="store_true", help="confirm the download shown by this command")

    for name, text in (("status", "is the game patched?"), ("revert", "remove the patch from the game folder")):
        game_arg(sub.add_parser(name, help=text))

    export = sub.add_parser("export", help="zip the deployed patch so it can be shared")
    game_arg(export)
    export.add_argument("-o", "--output", required=True, help="output .zip path")

    install = sub.add_parser("install", help="install a patch .zip into a game folder")
    install.add_argument("zip", help="patch package made by 'export'")
    game_arg(install)
    return parser


def _pipeline(args: argparse.Namespace) -> GodotTranslationPipeline:
    config = ConfigManager()
    pipeline = GodotTranslationPipeline(TranslationManager(config=config))
    pipeline.pck_key = args.pck_key or str(config.get("pck_encryption_key", "") or "")
    pipeline.gdre_path = getattr(args, "gdre", "") or str(config.get("gdre_path", "") or "")
    pipeline.json_text_keys = getattr(args, "json_keys", "") or str(config.get("json_text_keys", "") or "")
    pipeline.font_path = getattr(args, "font", "")
    pipeline.rtl_shaping = bool(config.get("rtl_shaping", True))
    return pipeline


def _game_dir(game: str, pipeline: GodotTranslationPipeline) -> tuple[Path, Path]:
    """(game folder, package file) for a folder, .pck or .exe argument."""
    profile = pipeline.profile_game(game)
    return Path(profile.game_dir), Path(profile.game_path)


def _inspect(args: argparse.Namespace) -> int:
    pipeline = _pipeline(args)
    profile = pipeline.profile_game(args.game)
    reader = PCKReader(profile.game_path, key=pipeline.pck_key)
    reader.open()
    status = patch_status(profile.game_dir, profile.game_path)
    candidates = find_json_key_candidates(reader, frozenset(parse_json_keys(pipeline.json_text_keys)))
    legacy = count_unreadable_scripts(reader)
    print(f"Package        : {profile.game_path}")
    print(f"Godot          : {profile.engine_version} (pack format {profile.pack_format}, {profile.file_count} files)")
    print(f"Encrypted files: {profile.encrypted_file_count}{' (directory too)' if profile.is_directory_encrypted else ''}")
    print(f"Patch          : {'yes, ' + status.locale if status.patched else 'no'}{' - game updated since' if status.game_updated else ''}")
    if find_language_root(profile.game_dir) is not None:
        print("Language packs : the game has its own (external/mods/lang_*); a new pack will be written there")
    print(f"Old scripts    : {legacy}" + ("  (GDRE Tools needed: run 'gdre-install')" if legacy and not find_gdre(pipeline.gdre_path) else ""))
    for group in find_unscanned_text_files(reader)[:10]:
        print(f"Not scanned    : {group.files} '{group.extension}' file(s) hold text, e.g. {group.sample_path}: {group.sample_text[:50]!r}")
    if candidates:
        print("JSON fields with text (+ = recommended; add them with --json-keys):")
        for c in candidates[:10]:
            mark = "+" if c.selected else "-"
            print(f"  {mark} {c.key:22} {c.count:6} values  [{c.reason}]  e.g. {c.sample[:40]!r}")
    return EXIT_OK


def _translate(args: argparse.Namespace) -> int:
    pipeline = _pipeline(args)
    if not args.quiet:
        pipeline.progress_callback = lambda e: _progress(e)
    session = pipeline.run(
        args.game, args.lang, source_lang=args.source, engine=TranslationEngine(args.engine), strategy=args.strategy
    )
    counts = session.counts()
    print(f"Done: {session.shippable_count}/{len(session.rows)} strings translated "
          f"({counts.get('rejected', 0) + counts.get('invalid', 0)} rejected, {counts.get('failed', 0)} failed).")
    return EXIT_OK


def _progress(event: ProgressEvent) -> None:
    total = event.total or 100
    print(f"[{int(100 * event.current / total):3d}%] {event.key}", file=sys.stderr)


def _gdre_install(args: argparse.Namespace) -> int:
    existing = find_gdre(str(ConfigManager().get("gdre_path", "") or ""))
    if existing is not None:
        print(f"GDRE Tools is already available: {existing}")
        return EXIT_OK
    release = fetch_latest_release()
    print(f"GDRE Tools {release.tag} ({release.license}): {release.asset_name}, {release.size / 1048576:.1f} MB")
    print(f"Source  : {release.url}\nSHA-256 : {release.sha256}")
    if not args.yes:
        print("Nothing was downloaded. Repeat with --yes to download and install it.", file=sys.stderr)
        return EXIT_USAGE
    executable = download_and_install(release, managed_install_root())
    config = ConfigManager()
    config.set("gdre_path", str(executable))
    print(f"Installed: {executable}")
    return EXIT_OK


def _status(args: argparse.Namespace) -> int:
    folder, pack = _game_dir(args.game, _pipeline(args))
    status = patch_status(folder, pack)
    if not status.patched:
        print("not patched")
    else:
        print(f"patched ({status.locale})" + (" - the game was updated since; rebuild the patch" if status.game_updated else ""))
    return EXIT_OK


def _export(args: argparse.Namespace) -> int:
    folder, pack = _game_dir(args.game, _pipeline(args))
    print(export_package(folder, args.output, pack.stem))
    return EXIT_OK


def _install(args: argparse.Namespace) -> int:
    folder, pack = _game_dir(args.game, _pipeline(args))
    result = install_package(args.zip, folder, pack)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"installed ({result.locale})")
    return EXIT_OK


def _revert(args: argparse.Namespace) -> int:
    folder, _pack = _game_dir(args.game, _pipeline(args))
    print(f"removed {OverlayPatcher(folder).remove_patch() + remove_packs(folder)} item(s)")
    return EXIT_OK


_HANDLERS = {
    "inspect": _inspect, "translate": _translate, "status": _status, "export": _export, "install": _install,
    "revert": _revert, "gdre-install": _gdre_install,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _HANDLERS[args.command](args)
    except NothingToTranslateError:
        print("No translatable text was found in this package.", file=sys.stderr)
        return EXIT_NOTHING
    except PCKEncryptionError as exc:
        print(f"Encryption: {exc} (use --pck-key)", file=sys.stderr)
        return EXIT_KEY
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
