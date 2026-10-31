"""Data structures representing files inside a Godot PCK package."""

from dataclasses import dataclass


@dataclass(slots=True)
class PCKEntry:
    """Represents a discrete indexed file entry inside a PCK package.

    Attributes:
        path: Virtual Godot resource path (e.g. 'res://scenes/main.tscn').
        offset: Absolute byte offset of the file payload in the source container.
        size: Size of the raw file payload in bytes.
        md5: 16-byte MD5 digest for data verification.
        flags: Feature flags (e.g., PACK_FILE_ENCRYPTED).
        source_data: Optional in-memory byte payload (used during repacking).
    """

    path: str
    offset: int
    size: int
    md5: bytes
    flags: int = 0
    source_data: bytes | None = None

    @property
    def is_encrypted(self) -> bool:
        """Indicates whether this specific file is marked as encrypted."""
        return bool(self.flags & 1)

    @property
    def md5_hex(self) -> str:
        """Hexadecimal string representation of the MD5 checksum."""
        return self.md5.hex()
