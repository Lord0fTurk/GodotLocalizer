"""Godot autonomous localization pipeline: inspect -> harvest -> translate -> deploy."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from src.core.bilingual import combine
from src.core.cache import TranslationCache
from src.core.external.gdre import GdreBackend, GdreError, find_gdre
from src.core.font_engine.glyph_coverage import GlyphCoverageChecker
from src.core.glossary import GlossaryManager
from src.core.langpack import LangPackSchema, build_pack, find_language_root, harvest_strings, learn_schema
from src.core.pck.crypto import PCKKeyRequiredError
from src.core.pck.reader import CustomPckSignatureError, PCKReader
from src.core.pipeline.harvest import (
    build_localized_files,
    build_optimized_translations,
    build_runtime_extras,
    build_runtime_messages,
    harvest_compiled_scripts_native,
    harvest_decompiled_scripts,
    harvest_pack,
    parse_json_keys,
)
from src.core.pipeline.review_session import ReviewSession, RunContext
from src.core.pipeline.translation_runner import BatchTranslationRunner, TranslationReport
from src.core.progress import (
    CancelToken,
    ProgressCallback,
    ProgressEvent,
    checkpoint,
    emit,
)
from src.core.rtl_shaper import is_rtl_language, shape_rtl
from src.core.translators.base import TranslationEngine
from src.core.translators.manager import TranslationManager
from src.harvesters.base import HarvestedEntry
from src.patcher.overlay_patcher import OverlayPatcher
from src.patcher.package import write_sidecar
from src.patcher.repack_patcher import RepackPatcher

logger = logging.getLogger(__name__)


class NothingToTranslateError(RuntimeError):
    """The package was read successfully but no translatable text was found in it."""


@dataclass(slots=True)
class GodotGameProfile:
    """Diagnostic profile of a target Godot game."""
    game_path: str
    game_name: str
    game_dir: str
    pack_format: int = 0
    engine_version: str = "Unknown"
    major_version: int = 4
    file_count: int = 0
    is_embedded: bool = False
    has_dialogue_manager: bool = False
    has_dialogic: bool = False
    has_official_translations: bool = False
    encrypted_file_count: int = 0
    is_directory_encrypted: bool = False


class NoGamePackageError(FileNotFoundError):
    """A folder holds neither a Godot .pck nor an executable with an embedded pack."""

    def __init__(self, folder: Path) -> None:
        self.folder = str(folder)
        super().__init__(f"No Godot .pck or exe with an embedded PCK found inside: {folder}")


class GodotTranslationPipeline:
    """Orchestrates Godot game inspection, text harvesting, batch translation, and patch deployment."""

    # Per-run user settings; AppBackend overwrites them before every run.
    pck_key: str = ""
    last_game_path: str = ""
    gdre_path: str = ""
    json_text_keys: str = ""
    font_path: str = ""
    rtl_shaping: bool = True
    bilingual: bool = False
    bilingual_template: str = "{target}\n({source})"

    def __init__(self, translation_manager: TranslationManager | None = None) -> None:
        self.tm = translation_manager or TranslationManager()
        self.runner = BatchTranslationRunner(self.tm, batch_size=self._configured_batch_size())
        self.progress_callback: ProgressCallback | None = None
        self.last_session: ReviewSession | None = None
        self.last_harvest_count: int = 0
        self.pck_key: str = ""  # 64-char hex AES key for encrypted PCKs
        self.json_text_keys: str = ""  # extra JSON key names that hold player-facing text
        self.gdre_path: str = ""  # optional GDRE Tools executable for compiled (.gdc) scripts
        self.font_path: str = ""  # optional fallback font injected into the patch
        self.rtl_shaping: bool = True  # pre-shape RTL text for engines without a text server (Godot 3)

    def _configured_batch_size(self) -> int:
        try:
            return max(1, int(self.tm.config.get("batch_size", 100) or 100))
        except (TypeError, ValueError):
            return 100

    @property
    def cache(self) -> TranslationCache:
        return self.tm.cache

    @property
    def glossary(self) -> GlossaryManager:
        return self.tm.glossary

    def save_review(self, session: ReviewSession) -> bool:
        """Persists manual review edits into the SQLite cache and updates game patch."""
        if not session:
            return False
        edited = session.edited()
        shippable = session.shippable()
        if edited:
            self.tm.cache.set_many(
                edited,
                source_lang=session.context.source_lang,
                target_lang=session.context.target_lang,
                engine=session.context.engine,
            )
        logger.info(f"Saved review session: {len(edited)} manual edits cached, {len(shippable)} shippable.")
        return True

    def _pick_game_file(self, directory: Path) -> Path:
        """Chooses the game package in a folder, ignoring GodotLocalizer's own output files."""
        own = {OverlayPatcher.DEFAULT_PATCH_PCK.lower(), "game_patched.pck"}
        if OverlayPatcher(directory).legacy_files():
            own.add(OverlayPatcher.LEGACY_PATCH_PCK)
        pcks = [p for p in directory.glob("*.pck") if p.name.lower() not in own]
        if pcks:
            return max(pcks, key=lambda p: p.stat().st_size)
        # Embedded packs: the exe that actually carries a PCK wins over launchers/helpers.
        custom: CustomPckSignatureError | None = None
        for exe in sorted(self._embedded_candidates(directory), key=lambda p: p.stat().st_size, reverse=True):
            try:
                PCKReader(exe, key=self.pck_key).open()
            except PCKKeyRequiredError:
                return exe  # it is a Godot pack; the key is requested when it is profiled
            except CustomPckSignatureError as exc:
                custom = custom or exc
                continue
            except (ValueError, OSError):
                continue
            return exe
        if custom is not None:
            raise custom
        raise NoGamePackageError(directory)

    @staticmethod
    def _embedded_candidates(directory: Path) -> list[Path]:
        """Executables that may carry an embedded PCK: Windows .exe and Linux ELF binaries (Game.x86_64, Game)."""
        found = list(directory.glob("*.exe"))
        for path in directory.iterdir():
            if path.is_file() and path.suffix.lower() in ("", ".x86_64", ".x86_32", ".arm64", ".arm32", ".bin"):
                with path.open("rb") as handle:
                    if handle.read(4) == b"ELF":
                        found.append(path)
        return found

    def profile_game(self, game_path_str: str) -> GodotGameProfile:
        """Inspects Godot PCK or embedded executable and returns game diagnostics."""
        path = Path(game_path_str).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Game file or directory not found: {path}")

        target_file = self._pick_game_file(path) if path.is_dir() else path

        reader = PCKReader(target_file, key=self.pck_key)
        reader.open()
        reader.verify_key()

        major, minor, patch = reader.engine_version
        engine_ver = f"{major}.{minor}.{patch}"

        has_dm = any(p.endswith(".dialogue") for p in reader.entries)
        has_dialogic = any(p.endswith(".dtl") or "dialogic" in p.lower() for p in reader.entries)
        has_trans = any(p.endswith((".translation", ".po", ".csv")) for p in reader.entries)

        return GodotGameProfile(
            game_path=str(target_file),
            game_name=target_file.name,
            game_dir=str(target_file.parent),
            pack_format=reader.format_version,
            engine_version=engine_ver,
            major_version=major,
            file_count=len(reader.entries),
            is_embedded=reader.is_embedded,
            has_dialogue_manager=has_dm,
            has_dialogic=has_dialogic,
            has_official_translations=has_trans,
            encrypted_file_count=reader.encrypted_file_count,
            is_directory_encrypted=reader.dir_encrypted,
        )

    def run(
        self,
        game_path: str | Path,
        target_lang: str,
        source_lang: str = "auto",
        engine: TranslationEngine = TranslationEngine.GOOGLE,
        cancel_token: CancelToken | None = None,
        strategy: str = "overlay",
    ) -> ReviewSession:
        """Executes full autonomous pipeline: Harvest -> Translate -> Build Patch."""
        cb = self.progress_callback

        emit(cb, "dashboard.status_profiling", 5, 100)
        checkpoint(cancel_token)
        profile = self.profile_game(str(game_path))
        reader = PCKReader(profile.game_path, key=self.pck_key)
        reader.open()

        emit(cb, "dashboard.status_translating", 15, 100)
        checkpoint(cancel_token)
        language_root = find_language_root(profile.game_dir)
        if language_root is not None:
            return self._run_language_pack(profile, reader, learn_schema(language_root), target_lang, source_lang, engine, cancel_token)
        by_key, unique = self._harvest(reader, source_lang, cancel_token)
        if not unique:
            raise NothingToTranslateError(profile.game_name)
        emit(cb, "dashboard.status_harvested_count", 25, 100, count=len(unique))
        self.last_game_path = profile.game_path

        checkpoint(cancel_token)
        report = self._translate(list(unique), profile, target_lang, source_lang, engine, cancel_token)
        self._apply_translations(by_key, unique, report.translations, target_lang, profile.major_version >= 4)

        emit(cb, "dashboard.patch_exporting", 90, 100)
        checkpoint(cancel_token)
        self._deploy(reader, profile, list(by_key.values()), target_lang, strategy)
        emit(cb, "dashboard.status_completed", 100, 100, count=len(report.translations))

        ctx = RunContext(
            game_root=Path(profile.game_dir),
            target_lang=target_lang,
            source_lang=source_lang,
            engine=engine.value,
            bilingual_mode=self.bilingual,
            bilingual_template=self.bilingual_template,
            rtl_shaping=is_rtl_language(target_lang),
        )
        session = ReviewSession.from_report(report, ctx)
        self.last_session = session
        return session

    def apply_session(self, session: ReviewSession, strategy: str = "overlay") -> int:
        """Rebuilds the game patch from a reviewed session (no translation engine involved)."""
        game = self.last_game_path or str(session.context.game_root)
        profile = self.profile_game(game)
        reader = PCKReader(profile.game_path, key=self.pck_key)
        reader.open()
        language_root = find_language_root(profile.game_dir)
        if language_root is not None:
            translations = session.shippable()
            self._deploy_language_pack(
                reader, learn_schema(language_root), translations, session.context.target_lang, session.context.source_lang
            )
            return len(translations)
        by_key, unique = self._harvest(reader, session.context.source_lang, None)
        translations = session.shippable()
        self._apply_translations(by_key, unique, translations, session.context.target_lang, profile.major_version >= 4)
        self._deploy(reader, profile, list(by_key.values()), session.context.target_lang, strategy)
        return len(translations)

    def _run_language_pack(
        self,
        profile: GodotGameProfile,
        reader: PCKReader,
        schema: LangPackSchema,
        target_lang: str,
        source_lang: str,
        engine: TranslationEngine,
        cancel_token: CancelToken | None,
    ) -> ReviewSession:
        """Games with their own ``external/mods/lang_*`` packs: translate the JSON data and write a new pack."""
        cb = self.progress_callback
        _by_key, unique = self._harvest(reader, source_lang, cancel_token)
        strings = list(dict.fromkeys([*harvest_strings(schema), *unique]))
        if not strings:
            raise NothingToTranslateError(profile.game_name)
        emit(cb, "dashboard.status_harvested_count", 25, 100, count=len(strings))
        self.last_game_path = profile.game_path

        checkpoint(cancel_token)
        report = self._translate(strings, profile, target_lang, source_lang, engine, cancel_token)
        emit(cb, "dashboard.patch_exporting", 90, 100)
        checkpoint(cancel_token)
        self._deploy_language_pack(reader, schema, report.translations, target_lang, source_lang)
        emit(cb, "dashboard.status_completed", 100, 100, count=len(report.translations))

        ctx = RunContext(
            game_root=Path(profile.game_dir),
            target_lang=target_lang,
            source_lang=source_lang,
            engine=engine.value,
            bilingual_mode=self.bilingual,
            bilingual_template=self.bilingual_template,
            rtl_shaping=is_rtl_language(target_lang),
        )
        session = ReviewSession.from_report(report, ctx)
        self.last_session = session
        return session

    def _deploy_language_pack(
        self, reader: PCKReader, schema: LangPackSchema, translations: dict[str, str], target_lang: str, source_lang: str
    ) -> None:
        """Writes the game's language pack; scene/UI strings from the ``.pck`` go into its ``ui_strings.json``."""
        _by_key, unique = self._harvest(reader, source_lang, None)
        rtl = is_rtl_language(target_lang)
        final = {
            src: combine(src, text, self.bilingual_template) if self.bilingual and not rtl else text
            for src, text in translations.items()
        }
        build_pack(schema, final, target_lang, {s: final[s] for s in unique if s in final})
        OverlayPatcher(game_dir=schema.root.parent).remove_patch()  # an older overlay would only fight the game's own language

    def _harvest(
        self, reader: PCKReader, source_lang: str, cancel_token: CancelToken | None
    ) -> tuple[dict[str, HarvestedEntry], dict[str, list[str]]]:
        """Harvests the pack; returns entries by key and the unique source text -> keys index."""
        by_key: dict[str, HarvestedEntry] = {}
        unique: dict[str, list[str]] = {}
        for entry in harvest_pack(
            reader,
            on_file=lambda: checkpoint(cancel_token),
            source_lang=source_lang,
            json_keys=parse_json_keys(self.json_text_keys),
        ):
            by_key[entry.key] = entry
            unique.setdefault(entry.source_text.strip(), []).append(entry.key)
        for entry in self._harvest_compiled_scripts(reader):
            by_key[entry.key] = entry
            unique.setdefault(entry.source_text.strip(), []).append(entry.key)
        self.last_harvest_count = len(by_key)
        logger.info(f"Harvested {len(by_key)} entries ({len(unique)} unique strings).")
        return by_key, unique

    def _harvest_compiled_scripts(self, reader: PCKReader) -> list[HarvestedEntry]:
        """Decompiles ``.gdc`` scripts with GDRE Tools when it is configured; never fails the run."""
        compiled = sum(1 for path in reader.entries if path.endswith(".gdc"))
        if not compiled:
            return []
        gdre = find_gdre(self.gdre_path)
        if gdre is not None:
            try:
                scripts = GdreBackend(gdre).decompile_scripts(reader.file_path, key=self.pck_key)
                entries = harvest_decompiled_scripts(reader, scripts)
                logger.info(f"GDRE Tools recovered {len(scripts)} scripts; {len(entries)} strings harvested.")
                return entries
            except GdreError as exc:
                logger.warning(f"GDRE Tools failed ({exc}); reading script constants directly instead.")
        entries = harvest_compiled_scripts_native(reader)
        logger.info(f"{compiled} compiled scripts: {len(entries)} strings read from their constant tables.")
        return entries

    def _translate(
        self,
        strings: list[str],
        profile: GodotGameProfile,
        target_lang: str,
        source_lang: str,
        engine: TranslationEngine,
        cancel_token: CancelToken | None,
    ) -> TranslationReport:
        cb = self.progress_callback

        def _on_sub_progress(event: ProgressEvent) -> None:
            # Map translation progress from 30 to 85
            emit(cb, event.key, int(30 + (event.fraction * 55)), 100, **event.params)

        return self.runner.run(
            strings=strings,
            target_lang=target_lang,
            engine=engine,
            progress=_on_sub_progress,
            source=profile.game_name,
            source_lang=source_lang,
            cancel=cancel_token,
        )

    def _apply_translations(
        self,
        by_key: dict[str, HarvestedEntry],
        unique: dict[str, list[str]],
        translations: dict[str, str],
        target_lang: str,
        is_godot4: bool,
    ) -> None:
        """Copies translations onto entries; Godot 3 cannot shape RTL text itself, so it is pre-shaped."""
        rtl = is_rtl_language(target_lang)
        shape = not is_godot4 and self.rtl_shaping and rtl
        for src_text, trans_text in translations.items():
            text = trans_text
            if self.bilingual and not rtl:  # mixed text directions are not worth the confusion
                text = combine(src_text, text, self.bilingual_template)
            if shape:
                text = shape_rtl(text)
            for key in unique.get(src_text, []):
                if key in by_key:
                    by_key[key].translation = text

    def _load_font(self, target_lang: str) -> bytes | None:
        """Bytes of the user's fallback font, with a warning when it lacks glyphs for the target language."""
        if not self.font_path or not Path(self.font_path).is_file():
            return None
        try:
            report = GlyphCoverageChecker.check_font_file(self.font_path, target_lang)
            if not report.is_fully_covered:
                logger.warning(
                    f"Font '{Path(self.font_path).name}' is missing glyphs for '{target_lang}': "
                    f"{''.join(report.missing_chars[:20])}"
                )
        except ValueError:
            logger.info(f"No glyph charset for '{target_lang}'; skipping font coverage check.")
        return Path(self.font_path).read_bytes()

    def _deploy(
        self,
        reader: PCKReader,
        profile: GodotGameProfile,
        entries: list[HarvestedEntry],
        target_lang: str,
        strategy: str,
    ) -> None:
        """Rebuilds changed files and ships them with the chosen patch strategy."""
        game_dir = Path(profile.game_dir)
        localized_files = build_localized_files(reader, entries, parse_json_keys(self.json_text_keys))
        if strategy != "overlay":
            RepackPatcher.repack(
                source_pck_path=profile.game_path,
                output_pck_path=game_dir / "Game_Patched.pck",
                updated_files=localized_files,
                key=self.pck_key,
            )
            return
        patcher = OverlayPatcher(
            game_dir=game_dir, is_godot4=profile.major_version >= 4, engine_version=reader.engine_version
        )
        patcher.build_overlay_patch(
            localized_files=localized_files,
            target_locale=target_lang,
            font_bytes=self._load_font(target_lang),
            runtime_translations=build_runtime_messages(entries),
            optimized_translations=build_optimized_translations(reader, entries, target_lang),
            extra_translations=build_runtime_extras(entries),
        )
        write_sidecar(game_dir, target_lang, profile.major_version >= 4, reader.engine_version, Path(profile.game_path))
