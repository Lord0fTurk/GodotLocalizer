"""Streaming reader and metadata inspector for Godot PCK packages."""

import hashlib
import io
import os
import struct
from pathlib import Path
from typing import BinaryIO

from src.core.pck.constants import (
    DEFAULT_CHUNK_SIZE,
    PACK_FILE_REMOVAL,
    PACK_FLAG_DIR_ENCRYPTED,
    PACK_FLAG_REL_FILEBASE,
    PACK_FLAG_SPARSE_BUNDLE,
    PCK_FORMAT_VERSION_2,
    PCK_FORMAT_VERSION_3,
    PCK_FORMAT_VERSION_4,
    PCK_MAGIC,
    RESERVED_PAD_COUNT,
    TRAILER_TOTAL_SIZE,
)
from src.core.pck.crypto import PCKKeyRequiredError, decrypt_blob, parse_key
from src.core.pck.entry import PCKEntry
from src.utils.logger import logger
from src.utils.paths import res_to_relative_path


class CustomPckSignatureError(ValueError):
    """The package looks like a Godot PCK but starts with another 4-byte signature (a custom engine build)."""

    def __init__(self, file_name: str, signature: bytes) -> None:
        self.signature = signature
        self.file_name = file_name
        super().__init__(
            f"'{file_name}' contains a Godot-style package with the signature "
            f"'{signature.decode('latin-1')}' instead of 'GDPC'. The game was exported with a custom Godot "
            "build (usually together with an encrypted directory), which GodotLocalizer cannot read."
        )


class PCKReader:
    """Reads, indexes, and extracts files from Godot 3.x/4.x PCKs and embedded EXEs."""

    def __init__(self, file_path: str | Path, key: str | bytes | None = None) -> None:
        self.file_path: Path = Path(file_path).resolve()
        self.key: bytes | None = parse_key(key)
        self.pck_offset: int = 0
        self.format_version: int = 0
        self.engine_version: tuple[int, int, int] = (0, 0, 0)
        self.pack_flags: int = 0
        self.file_base: int = 0
        self.directory_offset: int = 0
        self.entries: dict[str, PCKEntry] = {}
        self._is_embedded: bool = False

    def __enter__(self) -> "PCKReader":
        """Opens the PCK archive when used as a context manager."""
        self.open()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Context manager exit hook."""
        return None

    @property
    def dir_encrypted(self) -> bool:
        """True when the directory table itself is AES encrypted."""
        return bool(self.pack_flags & PACK_FLAG_DIR_ENCRYPTED)

    @property
    def encrypted_file_count(self) -> int:
        """Number of files whose payload is AES encrypted."""
        return sum(1 for entry in self.entries.values() if entry.is_encrypted)

    @property
    def is_embedded(self) -> bool:
        """Indicates if the PCK data is embedded inside an executable."""
        return self._is_embedded

    def open(self) -> None:
        """Locates PCK header, parses directory structure, and populates index."""
        if not self.file_path.exists():
            raise FileNotFoundError(f"Target PCK/EXE not found: {self.file_path}")

        with open(self.file_path, "rb") as stream:
            self.pck_offset, self._is_embedded = self._locate_pck_offset(stream)
            self._read_header(stream)
            self._read_directory(stream)
            logger.info(
                f"Opened PCK: '{self.file_path.name}' (Format {self.format_version}, "
                f"Godot {self.engine_version[0]}.{self.engine_version[1]}.{self.engine_version[2]}, "
                f"{len(self.entries)} files, embedded={self._is_embedded})"
            )

    def _locate_pck_offset(self, stream: BinaryIO) -> tuple[int, bool]:
        """Detects whether PCK is located at offset 0 or in an embedded PE trailer."""
        stream.seek(0, os.SEEK_END)
        total_len = stream.tell()

        # Check standard standalone PCK header
        stream.seek(0)
        if stream.read(4) == PCK_MAGIC:
            return 0, False

        # Check a PE section named "pck" (Godot 4.3+ "Embed PCK" exports)
        section_offset = self._find_pe_pck_section(stream)
        if section_offset is not None:
            return section_offset, True

        # Check an ELF section named "pck" (Godot 4.3+ "Embed PCK" exports for Linux/BSD)
        section_offset = self._find_elf_pck_section(stream)
        if section_offset is not None:
            return section_offset, True

        # Check embedded PE trailer in the final 12 bytes
        if total_len >= TRAILER_TOTAL_SIZE:
            stream.seek(total_len - 4)
            if stream.read(4) == PCK_MAGIC:
                stream.seek(total_len - TRAILER_TOTAL_SIZE)
                pck_size = struct.unpack("<Q", stream.read(8))[0]
                calculated_offset = total_len - TRAILER_TOTAL_SIZE - pck_size
                if calculated_offset >= 0:
                    stream.seek(calculated_offset)
                    if stream.read(4) == PCK_MAGIC:
                        return calculated_offset, True

        signature = self._custom_signature(stream, total_len)
        if signature is not None:
            raise CustomPckSignatureError(self.file_path.name, signature)
        raise ValueError(f"No valid Godot PCK signature found in '{self.file_path.name}'.")

    def _custom_signature(self, stream: BinaryIO, total_len: int) -> bytes | None:
        """A non-GDPC signature at a spot where Godot keeps its package, when the header around it is plausible."""
        starts: list[int] = []
        for finder in (self._find_pe_pck_section, self._find_elf_pck_section):
            offset = finder(stream, require_magic=False)
            if offset is not None:
                starts.append(offset)
        if total_len >= TRAILER_TOTAL_SIZE:  # trailer: <pack size:8><signature:4> at the very end
            stream.seek(total_len - TRAILER_TOTAL_SIZE)
            pack_size = struct.unpack("<Q", stream.read(8))[0]
            tail_magic = stream.read(4)
            start = total_len - TRAILER_TOTAL_SIZE - pack_size
            if start >= 0:
                stream.seek(start)
                if stream.read(4) == tail_magic:
                    starts.append(start)
        for start in starts:
            stream.seek(start)
            head = stream.read(8)
            if len(head) == 8 and head[:4].isascii() and 1 <= struct.unpack_from("<I", head, 4)[0] <= 4:
                return head[:4]
        return None

    @staticmethod
    def _find_pe_pck_section(stream: BinaryIO, require_magic: bool = True) -> int | None:
        """Returns the file offset of the PCK header inside a PE section called 'pck', if any."""
        stream.seek(0)
        dos = stream.read(64)
        if len(dos) < 64 or dos[:2] != b"MZ":
            return None
        pe_offset = struct.unpack_from("<I", dos, 0x3C)[0]
        stream.seek(pe_offset)
        coff = stream.read(24)
        if len(coff) < 24 or coff[:4] != b"PE\x00\x00":
            return None
        section_count, = struct.unpack_from("<H", coff, 6)
        optional_size, = struct.unpack_from("<H", coff, 20)
        stream.seek(pe_offset + 24 + optional_size)
        for _ in range(min(section_count, 96)):
            header = stream.read(40)
            if len(header) < 40:
                return None
            if header[:8].rstrip(b"\x00") != b"pck":
                continue
            raw_size, raw_ptr = struct.unpack_from("<II", header, 16)
            stream.seek(raw_ptr)
            window = stream.read(min(raw_size, 8) + 4)
            # Godot scans up to 8 bytes into the section for the magic.
            hit = window.find(PCK_MAGIC)
            if hit == -1:
                return None if require_magic else raw_ptr
            return raw_ptr + hit
        return None

    @staticmethod
    def _find_elf_pck_section(stream: BinaryIO, require_magic: bool = True) -> int | None:
        """Returns the file offset of the PCK header inside an ELF section called 'pck', if any."""
        stream.seek(0)
        ident = stream.read(16)
        if len(ident) < 16 or ident[:4] != b"ELF" or ident[4] not in (1, 2) or ident[5] not in (1, 2):
            return None
        is64 = ident[4] == 2
        order = "<" if ident[5] == 1 else ">"
        if is64:
            header = stream.read(48)
            shoff, = struct.unpack_from(order + "Q", header, 24)
            shentsize, shnum, shstrndx = struct.unpack_from(order + "HHH", header, 42)
            field = order + "QQ"  # sh_offset, sh_size at 24 / 32
            field_at = 24
        else:
            header = stream.read(36)
            shoff, = struct.unpack_from(order + "I", header, 16)
            shentsize, shnum, shstrndx = struct.unpack_from(order + "HHH", header, 30)
            field = order + "II"  # sh_offset, sh_size at 16 / 20
            field_at = 16
        if not shoff or not shnum or shstrndx >= shnum or shentsize < field_at + struct.calcsize(field):
            return None

        def section(index: int) -> tuple[int, int, int]:
            stream.seek(shoff + index * shentsize)
            raw = stream.read(shentsize)
            name_idx, = struct.unpack_from(order + "I", raw, 0)
            offset, size = struct.unpack_from(field, raw, field_at)
            return name_idx, offset, size

        _, names_at, names_size = section(shstrndx)
        stream.seek(names_at)
        names = stream.read(min(names_size, 1 << 16))
        for index in range(min(shnum, 256)):
            name_idx, offset, size = section(index)
            end = names.find(b"\x00", name_idx)
            if names[name_idx:end] not in (b"pck", b".pck"):
                continue
            stream.seek(offset)
            window = stream.read(min(size, 8) + 4)
            hit = window.find(PCK_MAGIC)  # Godot scans a few bytes into the section for the magic
            if hit == -1:
                return None if require_magic else offset
            return offset + hit
        return None

    def _read_header(self, stream: BinaryIO) -> None:
        """Parses the fixed-size PCK header fields."""
        stream.seek(self.pck_offset + 4)
        self.format_version = struct.unpack("<I", stream.read(4))[0]
        major, minor, patch = struct.unpack("<III", stream.read(12))
        self.engine_version = (major, minor, patch)

        if self.format_version > PCK_FORMAT_VERSION_4:
            raise ValueError(f"Unsupported PCK format version {self.format_version}.")

        if self.format_version >= PCK_FORMAT_VERSION_2:
            self.pack_flags = struct.unpack("<I", stream.read(4))[0]
            self.file_base = struct.unpack("<Q", stream.read(8))[0]
            if self.pack_flags & PACK_FLAG_SPARSE_BUNDLE:
                raise ValueError("Sparse-bundle PCKs are not supported yet.")
            # REL_FILEBASE is always implied from V3 on (Godot: "Always enabled for V3").
            if self.format_version >= PCK_FORMAT_VERSION_3 or (self.pack_flags & PACK_FLAG_REL_FILEBASE):
                self.file_base += self.pck_offset
            if self.format_version >= PCK_FORMAT_VERSION_3:
                self.directory_offset = struct.unpack("<Q", stream.read(8))[0]
                stream.seek((RESERVED_PAD_COUNT - 2) * 4, os.SEEK_CUR)
            else:
                self.directory_offset = 0
                stream.seek(RESERVED_PAD_COUNT * 4, os.SEEK_CUR)
        else:
            self.pack_flags = 0
            self.file_base = 0
            self.directory_offset = 0
            stream.seek(RESERVED_PAD_COUNT * 4, os.SEEK_CUR)

    def _read_directory(self, stream: BinaryIO) -> None:
        """Parses the directory entries table."""
        if self.directory_offset > 0:
            stream.seek(self.pck_offset + self.directory_offset)
        file_count = struct.unpack("<I", stream.read(4))[0]
        self.entries.clear()

        # The file count stays plain; with an encrypted directory the entries follow as one blob.
        table: BinaryIO = stream
        if self.dir_encrypted:
            if self.key is None:
                raise PCKKeyRequiredError(
                    "This PCK has an encrypted directory. Enter the 64-character hex encryption key."
                )
            table = io.BytesIO(decrypt_blob(stream, self.key))

        for _ in range(file_count):
            path_len = struct.unpack("<I", table.read(4))[0]
            padded_len = (path_len + 3) & ~3
            raw_path_bytes = table.read(padded_len)[:path_len].rstrip(b"\x00")
            clean_path = raw_path_bytes.decode("utf-8", errors="replace")
            if clean_path and not clean_path.startswith("res://"):
                clean_path = f"res://{clean_path.lstrip('/')}"

            rel_offset, size = struct.unpack("<QQ", table.read(16))
            md5_digest = table.read(16)
            flags = 0
            if self.format_version >= PCK_FORMAT_VERSION_2:
                flags = struct.unpack("<I", table.read(4))[0]

            if flags & PACK_FILE_REMOVAL:
                self.entries.pop(clean_path, None)
                continue

            if self.format_version >= PCK_FORMAT_VERSION_2:
                abs_offset = self.file_base + rel_offset
            else:
                abs_offset = self.pck_offset + rel_offset
            self.entries[clean_path] = PCKEntry(
                path=clean_path,
                offset=abs_offset,
                size=size,
                md5=md5_digest,
                flags=flags,
            )

    def extract_file(
        self,
        internal_path: str,
        dest_path: str | Path,
        verify_md5: bool = True,
    ) -> bool:
        """Extracts a single file to disk with streaming chunk buffer.

        Args:
            internal_path: The Godot virtual path (e.g. 'res://scenes/main.tscn').
            dest_path: Absolute destination path on disk.
            verify_md5: If True, asserts extracted MD5 matches header digest.

        Returns:
            True if extracted and verified successfully; False otherwise.
        """
        entry = self.entries.get(internal_path)
        if not entry:
            logger.warning(f"File not found in PCK: {internal_path}")
            return False

        out_path = Path(dest_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if entry.is_encrypted:
            # Encrypted payloads are decrypted (and MD5-checked) as one block.
            data = self.read_file_bytes(internal_path, verify_md5=verify_md5)
            if data is None:
                return False
            out_path.write_bytes(data)
            return True
        hasher = hashlib.md5()

        with open(self.file_path, "rb") as src, open(out_path, "wb") as dst:
            src.seek(entry.offset)
            remaining = entry.size
            while remaining > 0:
                chunk = src.read(min(remaining, DEFAULT_CHUNK_SIZE))
                if not chunk:
                    break
                dst.write(chunk)
                if verify_md5:
                    hasher.update(chunk)
                remaining -= len(chunk)

        if verify_md5 and hasher.digest() != entry.md5:
            logger.error(f"MD5 mismatch on extraction for '{internal_path}'.")
            out_path.unlink(missing_ok=True)
            return False
        return True

    def read_file_bytes(self, internal_path: str, verify_md5: bool = False) -> bytes | None:
        """Reads file payload directly into memory.

        Returns None for missing files, encrypted files without a key or with a wrong key, and,
        when ``verify_md5`` is set, payloads whose checksum does not match the directory.
        """
        entry = self.entries.get(internal_path)
        if not entry:
            return None
        if entry.is_encrypted:
            return self._read_encrypted(entry)
        with open(self.file_path, "rb") as src:
            src.seek(entry.offset)
            data = src.read(entry.size)
        if verify_md5 and hashlib.md5(data).digest() != entry.md5:
            logger.error(f"MD5 mismatch reading '{internal_path}'.")
            return None
        return data

    def verify_key(self) -> None:
        """Raises if the key cannot decrypt this pack's encrypted files (no-op for plain packs)."""
        sample = next((e for e in self.entries.values() if e.is_encrypted), None)
        if sample is None:
            return
        if self.key is None:
            raise PCKKeyRequiredError("This PCK contains encrypted files. Enter the 64-character hex encryption key.")
        with open(self.file_path, "rb") as src:
            src.seek(sample.offset)
            decrypt_blob(src, self.key)

    def _read_encrypted(self, entry: PCKEntry) -> bytes | None:
        """Decrypts one encrypted payload; the blob's own MD5 check doubles as key verification."""
        if self.key is None:
            logger.warning(f"Skipping encrypted file (no key set): {entry.path}")
            return None
        with open(self.file_path, "rb") as src:
            src.seek(entry.offset)
            try:
                return decrypt_blob(src, self.key)
            except ValueError as exc:
                logger.error(f"Cannot decrypt '{entry.path}': {exc}")
                return None

    def extract_all(
        self,
        target_dir: str | Path,
        verify_md5: bool = True,
    ) -> int:
        """Extracts all indexed files to the destination directory preserving hierarchy."""
        base_dir = Path(target_dir)
        extracted_count = 0

        for path in self.entries:
            rel_file_path = res_to_relative_path(path)
            target_dest = base_dir / rel_file_path
            if self.extract_file(path, target_dest, verify_md5=verify_md5):
                extracted_count += 1

        logger.info(f"Extracted {extracted_count}/{len(self.entries)} files to '{base_dir}'.")
        return extracted_count
