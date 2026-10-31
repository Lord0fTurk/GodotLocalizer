"""Packaging engine for building and repacking Godot PCK archives."""

import hashlib
import struct
from pathlib import Path
from typing import BinaryIO

from src.core.pck.constants import (
    DEFAULT_ALIGNMENT,
    DEFAULT_CHUNK_SIZE,
    PACK_FILE_ENCRYPTED,
    PACK_FLAG_DIR_ENCRYPTED,
    PACK_FLAG_REL_FILEBASE,
    PCK_FORMAT_VERSION_2,
    PCK_FORMAT_VERSION_3,
    PCK_MAGIC,
    RESERVED_PAD_COUNT,
)
from src.core.pck.crypto import PCKEncryptionError, blob_size, encrypt_blob, parse_key
from src.core.pck.entry import PCKEntry
from src.utils.logger import logger
from src.utils.paths import sanitize_godot_path


class PCKWriter:
    """Builds Godot 3.x and 4.x compatible .pck archives from disk or memory."""

    def __init__(
        self,
        output_path: str | Path,
        format_version: int = PCK_FORMAT_VERSION_2,
        engine_version: tuple[int, int, int] = (4, 2, 0),
        alignment: int = DEFAULT_ALIGNMENT,
        encryption_key: str | bytes | None = None,
        encrypt_directory: bool = False,
    ) -> None:
        self.output_path: Path = Path(output_path).resolve()
        self.format_version: int = format_version
        self.engine_version: tuple[int, int, int] = engine_version
        self.alignment: int = alignment
        self.key: bytes | None = parse_key(encryption_key)
        self.encrypt_directory: bool = encrypt_directory
        self._entries: dict[str, tuple[Path | None, bytes | None, bool]] = {}
        if (encrypt_directory or self.key) and format_version < PCK_FORMAT_VERSION_2:
            raise PCKEncryptionError("PCK encryption requires format version 2 or newer (Godot 4).")
        if encrypt_directory and self.key is None:
            raise PCKEncryptionError("An encryption key is required to encrypt the directory.")

    def _check_encrypted(self, encrypted: bool) -> None:
        if encrypted and self.key is None:
            raise PCKEncryptionError("An encryption key is required to add encrypted files.")

    def add_file(self, godot_path: str, disk_path: str | Path, encrypted: bool = False) -> None:
        """Queues a physical disk file to be included in the PCK."""
        clean_path = sanitize_godot_path(godot_path)
        src = Path(disk_path).resolve()
        if not src.exists() or not src.is_file():
            raise FileNotFoundError(f"Source file not found: {disk_path}")
        self._check_encrypted(encrypted)
        self._entries[clean_path] = (src, None, encrypted)

    def add_bytes(self, godot_path: str, data: bytes, encrypted: bool = False) -> None:
        """Queues an in-memory byte buffer to be included in the PCK."""
        clean_path = sanitize_godot_path(godot_path)
        self._check_encrypted(encrypted)
        self._entries[clean_path] = (None, data, encrypted)

    def build(self) -> int:
        """Compiles and writes the complete PCK package to disk."""
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.output_path.with_suffix(self.output_path.suffix + ".tmp")

        try:
            with open(temp_path, "wb") as stream:
                # 1. Write preliminary header placeholder
                dir_start_pos = self._write_header_placeholder(stream)

                # 2. Write file payloads and compute offsets/hashes
                processed_entries, file_base = self._write_payloads(stream)

                # 3. Finalize header with file_base and directory_offset
                self._finalize_header(stream, file_base, dir_start_pos)

                # 4. Write directory table at end of header
                stream.seek(dir_start_pos)
                self._write_directory(stream, processed_entries, file_base)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

        if self.output_path.exists():
            self.output_path.unlink()
        temp_path.rename(self.output_path)

        logger.info(
            f"Successfully built PCK '{self.output_path.name}' with "
            f"{len(processed_entries)} files (Format {self.format_version})."
        )
        return len(processed_entries)

    def _write_header_placeholder(self, stream: BinaryIO) -> int:
        """Writes fixed header fields and leaves room for directory index."""
        stream.seek(0)
        stream.write(PCK_MAGIC)
        stream.write(struct.pack("<I", self.format_version))
        stream.write(struct.pack("<III", *self.engine_version))

        if self.format_version >= PCK_FORMAT_VERSION_3:
            pack_flags = self._pack_flags()
            stream.write(struct.pack("<I", pack_flags))
            stream.write(struct.pack("<Q", 0))  # Placeholder for file_base
            stream.write(struct.pack("<Q", 0))  # Placeholder for directory_offset
            stream.write(b"\x00" * ((RESERVED_PAD_COUNT - 2) * 4))
        elif self.format_version >= PCK_FORMAT_VERSION_2:
            pack_flags = self._pack_flags()
            stream.write(struct.pack("<I", pack_flags))
            stream.write(struct.pack("<Q", 0))  # Placeholder for file_base
            stream.write(b"\x00" * (RESERVED_PAD_COUNT * 4))
        else:
            stream.write(b"\x00" * (RESERVED_PAD_COUNT * 4))
        return stream.tell()

    def _pack_flags(self) -> int:
        flags = PACK_FLAG_REL_FILEBASE
        if self.encrypt_directory:
            flags |= PACK_FLAG_DIR_ENCRYPTED
        return flags

    def _finalize_header(self, stream: BinaryIO, file_base: int, dir_offset: int = 0) -> None:
        """Updates file_base and directory_offset in header for Godot 4 format."""
        if self.format_version >= PCK_FORMAT_VERSION_3:
            stream.seek(24)
            stream.write(struct.pack("<QQ", file_base, dir_offset))
        elif self.format_version >= PCK_FORMAT_VERSION_2:
            # file_base offset is at byte 24 (4 magic + 4 fmt + 12 ver + 4 flags)
            stream.seek(24)
            stream.write(struct.pack("<Q", file_base))

    def _write_directory(
        self,
        stream: BinaryIO,
        entries: list[PCKEntry],
        file_base: int,
    ) -> None:
        """Writes the file directory table into the stream (count stays plain, entries may be encrypted)."""
        stream.write(struct.pack("<I", len(entries)))
        table = b"".join(self._serialize_entry(entry, file_base) for entry in entries)
        if self.encrypt_directory:
            assert self.key is not None
            table = encrypt_blob(table, self.key)
        stream.write(table)

    def _serialize_entry(self, entry: PCKEntry, file_base: int) -> bytes:
        path_bytes = entry.path.encode("utf-8")
        padded_len = (len(path_bytes) + 3) & ~3
        # Godot reads exactly the stored length, so it must already include the 4-byte alignment padding.
        out = struct.pack("<I", padded_len) + path_bytes + b"\x00" * (padded_len - len(path_bytes))

        # For Godot 4, offsets are relative to file_base; for Godot 3, relative to PCK start
        rel_offset = entry.offset - file_base if self.format_version >= PCK_FORMAT_VERSION_2 else entry.offset
        out += struct.pack("<QQ", rel_offset, entry.size) + entry.md5
        if self.format_version >= PCK_FORMAT_VERSION_2:
            out += struct.pack("<I", entry.flags)
        return out

    def _directory_size(self) -> int:
        """Bytes the directory occupies, including the plain file count."""
        table = 0
        for path in self._entries:
            padded_len = (len(path.encode("utf-8")) + 3) & ~3
            # path_len (4) + path + offset (8) + size (8) + md5 (16) [+ flags (4)]
            table += 4 + padded_len + 8 + 8 + 16
            if self.format_version >= PCK_FORMAT_VERSION_2:
                table += 4
        return 4 + (blob_size(table) if self.encrypt_directory else table)
    def _write_payloads(
        self,
        stream: BinaryIO,
    ) -> tuple[list[PCKEntry], int]:
        """Calculates directory size, determines file_base, and writes payloads."""
        dir_size = self._directory_size()

        # Directory ends at current stream pos + dir_size
        dir_end_pos = stream.tell() + dir_size

        # Align file_base to alignment boundary
        file_base = self._align_offset(dir_end_pos)
        stream.seek(file_base)

        processed: list[PCKEntry] = []
        for path, (disk_path, raw_bytes, encrypted) in self._entries.items():
            current_offset = self._align_offset(stream.tell())
            stream.seek(current_offset)

            if encrypted:
                data = raw_bytes if raw_bytes is not None else Path(str(disk_path)).read_bytes()
                md5_digest, size = self._write_encrypted(stream, data)
            elif raw_bytes is not None:
                md5_digest, size = self._write_buffer(stream, raw_bytes)
            elif disk_path is not None:
                md5_digest, size = self._write_disk_file(stream, disk_path)
            else:
                continue

            processed.append(
                PCKEntry(
                    path=path,
                    offset=current_offset,
                    size=size,
                    md5=md5_digest,
                    flags=PACK_FILE_ENCRYPTED if encrypted else 0,
                )
            )

        return processed, file_base

    def _align_offset(self, offset: int) -> int:
        """Aligns offset to the configured byte boundary."""
        if self.alignment <= 1:
            return offset
        remainder = offset % self.alignment
        return offset if remainder == 0 else offset + (self.alignment - remainder)

    def _write_encrypted(self, stream: BinaryIO, data: bytes) -> tuple[bytes, int]:
        """Writes an encrypted blob; the directory records the plaintext MD5 and length like Godot."""
        assert self.key is not None
        stream.write(encrypt_blob(data, self.key))
        return hashlib.md5(data).digest(), len(data)

    def _write_buffer(self, stream: BinaryIO, data: bytes) -> tuple[bytes, int]:
        """Writes in-memory bytes to stream and returns MD5 and length."""
        stream.write(data)
        hasher = hashlib.md5(data)
        return hasher.digest(), len(data)

    def _write_disk_file(self, stream: BinaryIO, src_path: Path) -> tuple[bytes, int]:
        """Streams a disk file to the output stream and calculates MD5."""
        hasher = hashlib.md5()
        total_written = 0
        with open(src_path, "rb") as src:
            while True:
                chunk = src.read(DEFAULT_CHUNK_SIZE)
                if not chunk:
                    break
                stream.write(chunk)
                hasher.update(chunk)
                total_written += len(chunk)
        return hasher.digest(), total_written
