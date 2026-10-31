"""PCK repacking engine for clean full-archive patch delivery."""

from pathlib import Path

from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter
from src.utils.logger import logger


class RepackPatcher:
    """Rebuilds a full PCK package substituting translated assets for originals."""

    @classmethod
    def repack(
        cls,
        source_pck_path: str | Path,
        output_pck_path: str | Path,
        updated_files: dict[str, bytes],
        extra_files: dict[str, bytes] | None = None,
        key: str | bytes | None = None,
    ) -> int:
        """Reads source PCK, swaps updated files, and produces a complete new PCK.

        Args:
            source_pck_path: Path to existing game .pck or embedded executable.
            output_pck_path: Destination path for the newly rebuilt .pck.
            updated_files: Dictionary mapping virtual 'res://' paths to modified bytes.
            extra_files: Optional brand-new files (e.g. fonts) to insert.
            key: Encryption key of the source pack. Files that were encrypted (and an encrypted\n                directory) stay encrypted with the same key in the rebuilt pack.

        Returns:
            Total count of files bundled into the rebuilt PCK.
        """
        reader = PCKReader(source_pck_path, key=key)
        reader.open()

        out_path = Path(output_pck_path).resolve()
        writer = PCKWriter(
            output_path=out_path,
            format_version=reader.format_version,
            engine_version=reader.engine_version,
            encryption_key=reader.key,
            encrypt_directory=reader.dir_encrypted,
        )

        total_files = 0

        # 1. Process all existing files from the original package
        for res_path in reader.entries:
            encrypted = reader.entries[res_path].is_encrypted
            if res_path in updated_files:
                writer.add_bytes(res_path, updated_files[res_path], encrypted=encrypted)
            else:
                original_bytes = reader.read_file_bytes(res_path)
                if original_bytes is None:
                    raise ValueError(f"Cannot read '{res_path}' from the source pack (wrong or missing key?).")
                writer.add_bytes(res_path, original_bytes, encrypted=encrypted)
            total_files += 1

        # 2. Add extra new files that didn't exist in original PCK
        if extra_files:
            for extra_path, extra_data in extra_files.items():
                if extra_path not in reader.entries:
                    writer.add_bytes(extra_path, extra_data)
                    total_files += 1

        writer.build()
        logger.info(
            f"Repacked PCK successfully written to '{out_path.name}' "
            f"({total_files} total files, {len(updated_files)} updated)."
        )
        return total_files
