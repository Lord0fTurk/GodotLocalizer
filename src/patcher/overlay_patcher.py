"""Non-destructive PCK overlay patcher utilizing override.cfg."""

from pathlib import Path

from src.core.pck.constants import (
    PCK_FORMAT_VERSION_1,
    PCK_FORMAT_VERSION_2,
    PCK_FORMAT_VERSION_3,
)
from src.core.pck.writer import PCKWriter
from src.patcher.bootstrap_script import (
    DEFAULT_PATCH_PCK_NAME,
    TRANSLATIONS_RES_PATH,
    BootstrapScriptGenerator,
    build_translations_payload,
)
from src.patcher.override_cfg import AUTOLOAD_KEY, add_autoload, remove_autoload
from src.utils.logger import logger


class OverlayPatcher:
    """Deploys non-destructive patches via a patch PCK overlay and override.cfg."""

    OVERRIDE_CFG_NAME = "override.cfg"
    BOOTSTRAP_SCRIPT_NAME = "godotlocalizer_bootstrap.gd"
    LEGACY_BOOTSTRAP_SCRIPT_NAME = "patch_bootstrap.gd"
    SIDECAR_NAME = "godotlocalizer_patch.json"  # what was deployed (written by package.write_sidecar)
    DEFAULT_PATCH_PCK = DEFAULT_PATCH_PCK_NAME

    def __init__(
        self,
        game_dir: str | Path,
        is_godot4: bool = True,
        engine_version: tuple[int, int, int] = (4, 2, 0),
    ) -> None:
        self.game_dir = Path(game_dir).resolve()
        self.is_godot4 = is_godot4
        self.engine_version = engine_version

    LEGACY_PATCH_PCK = "patch.pck"
    _LEGACY_MARKER = "# GodotLocalizer Runtime Bootstrap"

    def legacy_files(self) -> list[Path]:
        """Files left by pre-0.1 versions (``patch_bootstrap.gd`` + ``patch.pck``), recognised by our own script.

        ``patch.pck`` is only included when the old bootstrap script that loads it is present, so a game's own
        ``patch.pck`` is never touched.
        """
        script = self.game_dir / self.LEGACY_BOOTSTRAP_SCRIPT_NAME
        try:
            content = script.read_text(encoding="utf-8")
        except OSError:
            return []
        if self._LEGACY_MARKER not in content:
            return []
        files = [script]
        if self.LEGACY_PATCH_PCK in content and (self.game_dir / self.LEGACY_PATCH_PCK).is_file():
            files.append(self.game_dir / self.LEGACY_PATCH_PCK)
        return files

    def _format_version(self) -> int:
        if not self.is_godot4:
            return PCK_FORMAT_VERSION_1
        return PCK_FORMAT_VERSION_3 if self.engine_version >= (4, 5, 0) else PCK_FORMAT_VERSION_2

    def build_overlay_patch(
        self,
        localized_files: dict[str, bytes],
        target_locale: str = "tr",
        font_bytes: bytes | None = None,
        patch_pck_name: str = DEFAULT_PATCH_PCK,
        runtime_translations: dict[str, str] | None = None,
        optimized_translations: list[dict[str, object]] | None = None,
    ) -> bool:
        """Packages localized files into the patch PCK and deploys override.cfg.

        Args:
            localized_files: Dictionary of virtual Godot path to translated byte payload.
            target_locale: Locale code to enforce at startup (e.g. 'tr', 'pt-BR').
            font_bytes: Optional raw TTF/OTF bytes to inject as a fallback font.
            patch_pck_name: Name of the patch PCK file to produce.
            runtime_translations: Optional source-text/key -> translation table that the
                bootstrap registers with ``TranslationServer`` at startup.
            optimized_translations: Rebuilt ``OptimizedTranslation`` tables (see ``pipeline.harvest``)
                that the bootstrap re-creates at runtime.

        Returns:
            True on successful patch deployment. On failure everything written is rolled back.
        """
        patch_pck_path = self.game_dir / patch_pck_name
        disk_bootstrap_path = self.game_dir / self.BOOTSTRAP_SCRIPT_NAME
        override_path = self.game_dir / self.OVERRIDE_CFG_NAME
        previous_override = override_path.read_bytes() if override_path.exists() else None

        for old in self.legacy_files():  # upgrade: the new bootstrap replaces the old one in override.cfg
            old.unlink(missing_ok=True)
        try:
            bootstrap_bytes = self._write_patch_pck(
                patch_pck_path, localized_files, target_locale, font_bytes, patch_pck_name, runtime_translations,
                optimized_translations,
            )
            # The bootstrap also lives next to the executable: res:// falls back to the disk
            # for files that are not in a mounted pack, which is how the engine finds it on boot.
            disk_bootstrap_path.write_bytes(bootstrap_bytes)
            self._write_override_cfg()
        except Exception:
            patch_pck_path.unlink(missing_ok=True)
            disk_bootstrap_path.unlink(missing_ok=True)
            if previous_override is None:
                override_path.unlink(missing_ok=True)
            else:
                override_path.write_bytes(previous_override)
            raise

        logger.info(f"Overlay patch successfully deployed to '{self.game_dir}'.")
        return True

    def _write_patch_pck(
        self,
        patch_pck_path: Path,
        localized_files: dict[str, bytes],
        target_locale: str,
        font_bytes: bytes | None,
        patch_pck_name: str,
        runtime_translations: dict[str, str] | None,
        optimized_translations: list[dict[str, object]] | None = None,
    ) -> bytes:
        """Builds the patch PCK and returns the bootstrap script bytes."""
        writer = PCKWriter(
            output_path=patch_pck_path,
            format_version=self._format_version(),
            engine_version=self.engine_version,
        )

        font_res_path = None
        if font_bytes:
            font_res_path = "res://patch_assets/fonts/LocalizedFont.ttf"
            writer.add_bytes(font_res_path, font_bytes)

        for res_path, data in localized_files.items():
            writer.add_bytes(res_path, data)

        if runtime_translations or optimized_translations:
            payload = build_translations_payload(target_locale, runtime_translations or {}, optimized_translations)
            writer.add_bytes(TRANSLATIONS_RES_PATH, payload)

        generate = (
            BootstrapScriptGenerator.generate_godot4_bootstrap
            if self.is_godot4
            else BootstrapScriptGenerator.generate_godot3_bootstrap
        )
        script_code = generate(pck_file_name=patch_pck_name, target_locale=target_locale, font_res_path=font_res_path)
        bootstrap_bytes = script_code.encode("utf-8")
        writer.add_bytes(f"res://{self.BOOTSTRAP_SCRIPT_NAME}", bootstrap_bytes)
        writer.build()
        return bootstrap_bytes

    def _write_override_cfg(self) -> None:
        """Merges the autoload hook into override.cfg without touching other entries."""
        override_path = self.game_dir / self.OVERRIDE_CFG_NAME
        existing = override_path.read_text(encoding="utf-8-sig") if override_path.exists() else ""
        merged = add_autoload(existing, f"res://{self.BOOTSTRAP_SCRIPT_NAME}", prepend=self.is_godot4)
        override_path.write_bytes(merged.encode("utf-8"))

    def remove_patch(self, patch_pck_name: str = DEFAULT_PATCH_PCK) -> int:
        """Removes GodotLocalizer's files and its override.cfg entry; returns the removed item count.

        A pre-existing override.cfg is kept (minus our entry) and only deleted when nothing else is left.
        """
        removed = 0
        for old in self.legacy_files():
            old.unlink(missing_ok=True)
            removed += 1
        for name in (patch_pck_name, self.BOOTSTRAP_SCRIPT_NAME, self.SIDECAR_NAME):
            target = self.game_dir / name
            if target.exists():
                target.unlink()
                removed += 1

        override_path = self.game_dir / self.OVERRIDE_CFG_NAME
        if override_path.exists():
            original = override_path.read_text(encoding="utf-8-sig")
            cleaned = remove_autoload(original)
            if AUTOLOAD_KEY in original:
                removed += 1
            if cleaned.strip():
                override_path.write_bytes(cleaned.encode("utf-8"))
            else:
                override_path.unlink()

        logger.info(f"Overlay patch removed from '{self.game_dir}'.")
        return removed
