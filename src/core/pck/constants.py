"""Binary constants and structure definitions for Godot PCK format."""

from typing import Final

# PCK Magic Header ('GDPC' -> 0x43504447 in little-endian)
PCK_MAGIC: Final[bytes] = b"GDPC"

# Pack Format Versions
PCK_FORMAT_VERSION_0: Final[int] = 0  # Godot 1.x / 2.x
PCK_FORMAT_VERSION_1: Final[int] = 1  # Godot 3.x
PCK_FORMAT_VERSION_2: Final[int] = 2  # Godot 4.0 - 4.4
PCK_FORMAT_VERSION_3: Final[int] = 3  # Godot 4.5+ (adds directory_offset uint64)
PCK_FORMAT_VERSION_4: Final[int] = 4  # Godot master (adds sparse bundle / delta flags)

# Godot 4 Pack Flags (core/io/file_access_pack.h: PackFlags)
PACK_FLAG_DIR_ENCRYPTED: Final[int] = 1 << 0
PACK_FLAG_REL_FILEBASE: Final[int] = 1 << 1
PACK_FLAG_SPARSE_BUNDLE: Final[int] = 1 << 2

# Godot 4 File Flags (core/io/file_access_pack.h: PackFileFlags)
PACK_FILE_ENCRYPTED: Final[int] = 1 << 0
PACK_FILE_REMOVAL: Final[int] = 1 << 1
PACK_FILE_DELTA: Final[int] = 1 << 2

# Embedded Executable Trailer
TRAILER_MAGIC_SIZE: Final[int] = 4
TRAILER_SIZE_FIELD: Final[int] = 8
TRAILER_TOTAL_SIZE: Final[int] = 12

# Alignment & Buffer limits
DEFAULT_ALIGNMENT: Final[int] = 32
DEFAULT_CHUNK_SIZE: Final[int] = 1024 * 1024  # 1 MB
RESERVED_PAD_COUNT: Final[int] = 16  # 16 uint32 values (64 bytes)
