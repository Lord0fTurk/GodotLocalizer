"""AES-256-CFB container used by Godot for encrypted PCK directories and files.

Layout of one encrypted blob (``FileAccessEncrypted`` without magic), little-endian:

    md5(plaintext)[16] | plaintext_length u64 | iv[16] | AES-256-CFB(plaintext zero-padded to 16)
"""

import hashlib
import os
import struct
from typing import BinaryIO

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

try:  # newer cryptography releases moved CFB (Godot's encryption mode) to the "decrepit" namespace
    from cryptography.hazmat.decrepit.ciphers.modes import CFB
except ImportError:
    from cryptography.hazmat.primitives.ciphers.modes import CFB

KEY_SIZE = 32
BLOCK_SIZE = 16
BLOB_HEADER_SIZE = 16 + 8 + 16


class PCKEncryptionError(ValueError):
    """Base error for encrypted-pack problems (missing key, wrong key, corrupt data)."""


class PCKKeyRequiredError(PCKEncryptionError):
    """The pack is encrypted and no key was supplied."""


class PCKWrongKeyError(PCKEncryptionError):
    """Decryption produced data that fails the MD5 check: wrong key or corrupt pack."""


def parse_key(key: str | bytes | None) -> bytes | None:
    """Accepts the 64-character hex key Godot uses (or raw 32 bytes); None/'' means no key."""
    if key is None:
        return None
    if isinstance(key, bytes):
        if len(key) != KEY_SIZE:
            raise PCKEncryptionError(f"Encryption key must be {KEY_SIZE} bytes, got {len(key)}.")
        return key
    cleaned = key.strip().removeprefix("0x")
    if not cleaned:
        return None
    try:
        raw = bytes.fromhex(cleaned)
    except ValueError as exc:
        raise PCKEncryptionError("Encryption key must be a 64-character hexadecimal string.") from exc
    if len(raw) != KEY_SIZE:
        raise PCKEncryptionError(f"Encryption key must be 64 hex characters (32 bytes), got {len(cleaned)}.")
    return raw


def padded_length(plain_length: int) -> int:
    """Ciphertext length for a plaintext of ``plain_length`` bytes."""
    return plain_length + (-plain_length % BLOCK_SIZE)


def blob_size(plain_length: int) -> int:
    """Total on-disk size of an encrypted blob holding ``plain_length`` plaintext bytes."""
    return BLOB_HEADER_SIZE + padded_length(plain_length)


def encrypt_blob(plaintext: bytes, key: bytes, iv: bytes | None = None) -> bytes:
    """Encrypts ``plaintext`` into Godot's blob format."""
    iv = iv if iv is not None else os.urandom(BLOCK_SIZE)
    padded = plaintext + b"\x00" * (padded_length(len(plaintext)) - len(plaintext))
    encryptor = Cipher(algorithms.AES(key), CFB(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return hashlib.md5(plaintext).digest() + struct.pack("<Q", len(plaintext)) + iv + ciphertext


def decrypt_blob(stream: BinaryIO, key: bytes) -> bytes:
    """Reads and decrypts one blob at the stream's current position."""
    header = stream.read(BLOB_HEADER_SIZE)
    if len(header) < BLOB_HEADER_SIZE:
        raise PCKEncryptionError("Truncated encrypted block.")
    md5 = header[:16]
    (length,) = struct.unpack_from("<Q", header, 16)
    iv = header[24:40]
    ciphertext = stream.read(padded_length(length))
    if len(ciphertext) < padded_length(length):
        raise PCKEncryptionError("Truncated encrypted block.")
    decryptor = Cipher(algorithms.AES(key), CFB(iv)).decryptor()
    plaintext = (decryptor.update(ciphertext) + decryptor.finalize())[:length]
    if hashlib.md5(plaintext).digest() != md5:
        raise PCKWrongKeyError("Decryption failed: the encryption key is wrong or the pack is corrupt.")
    return plaintext
