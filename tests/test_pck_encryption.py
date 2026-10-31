"""Encrypted PCK tests: hand-assembled spec fixtures plus writer/reader/repack round trips."""

import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

from src.core.pck.constants import PACK_FILE_ENCRYPTED, PCK_MAGIC
from src.core.pck.crypto import (
    CFB,
    PCKEncryptionError,
    PCKKeyRequiredError,
    PCKWrongKeyError,
    parse_key,
)
from src.core.pck.reader import PCKReader
from src.core.pck.writer import PCKWriter
from src.patcher.repack_patcher import RepackPatcher
from src.utils.logger import mask_secrets

KEY_HEX = "00112233445566778899aabbccddeeff" * 2
OTHER_KEY_HEX = "ff" * 32
IV = bytes(range(16))


def spec_blob(plaintext: bytes, key: bytes) -> bytes:
    """Godot FileAccessEncrypted (no magic): md5 | u64 length | iv | AES-256-CFB(zero padded)."""
    padded = plaintext + b"\x00" * (-len(plaintext) % 16)
    enc = Cipher(algorithms.AES(key), CFB(IV)).encryptor()
    return hashlib.md5(plaintext).digest() + struct.pack("<Q", len(plaintext)) + IV + enc.update(padded) + enc.finalize()


def spec_entry(path: str, ofs: int, plain: bytes, flags: int) -> bytes:
    raw = path.encode()
    return (
        struct.pack("<I", len(raw) + (-len(raw) % 4)) + raw + b"\x00" * (-len(raw) % 4)
        + struct.pack("<QQ", ofs, len(plain)) + hashlib.md5(plain).digest() + struct.pack("<I", flags)
    )


def build_spec_pack(key: bytes, plain: bytes, encrypt_dir: bool) -> bytes:
    """Version-2 pack with one encrypted file, assembled byte by byte from the Godot spec."""
    header_len = 4 + 4 + 12 + 4 + 8 + 64
    table_len = len(spec_entry("res://secret.txt", 0, plain, 1))
    dir_len = 4 + (40 + table_len + (-table_len % 16) if encrypt_dir else table_len)
    file_base = header_len + dir_len
    table = spec_entry("res://secret.txt", 0, plain, PACK_FILE_ENCRYPTED)
    flags = 0b10 | (0b01 if encrypt_dir else 0)  # REL_FILEBASE | DIR_ENCRYPTED
    head = PCK_MAGIC + struct.pack("<IIII", 2, 4, 3, 0) + struct.pack("<IQ", flags, file_base) + b"\x00" * 64
    directory = struct.pack("<I", 1) + (spec_blob(table, key) if encrypt_dir else table)
    assert len(head) + len(directory) == file_base
    return head + directory + spec_blob(plain, key)


class TestSpecFixtures(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.key = bytes.fromhex(KEY_HEX)
        self.plain = b"top secret dialogue \xc3\xa7"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, encrypt_dir: bool) -> Path:
        path = self.dir / "game.pck"
        path.write_bytes(build_spec_pack(self.key, self.plain, encrypt_dir))
        return path

    def test_encrypted_files_plain_directory(self) -> None:
        reader = PCKReader(self._write(False), key=KEY_HEX)
        reader.open()
        self.assertFalse(reader.dir_encrypted)
        self.assertEqual(reader.encrypted_file_count, 1)
        self.assertEqual(reader.read_file_bytes("res://secret.txt"), self.plain)

    def test_encrypted_directory_and_files(self) -> None:
        reader = PCKReader(self._write(True), key=KEY_HEX)
        reader.open()
        self.assertTrue(reader.dir_encrypted)
        self.assertEqual(reader.read_file_bytes("res://secret.txt"), self.plain)
        self.assertTrue(reader.extract_file("res://secret.txt", self.dir / "out" / "secret.txt"))
        self.assertEqual((self.dir / "out" / "secret.txt").read_bytes(), self.plain)

    def test_missing_key_is_a_clear_error(self) -> None:
        with self.assertRaises(PCKKeyRequiredError):
            PCKReader(self._write(True)).open()
        reader = PCKReader(self._write(False))
        reader.open()
        self.assertIsNone(reader.read_file_bytes("res://secret.txt"))
        with self.assertRaises(PCKKeyRequiredError):
            reader.verify_key()

    def test_wrong_key_is_detected(self) -> None:
        with self.assertRaises(PCKWrongKeyError):
            PCKReader(self._write(True), key=OTHER_KEY_HEX).open()
        reader = PCKReader(self._write(False), key=OTHER_KEY_HEX)
        reader.open()
        self.assertIsNone(reader.read_file_bytes("res://secret.txt"))
        with self.assertRaises(PCKWrongKeyError):
            reader.verify_key()


class TestWriterRoundTrip(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _build(self, name: str, version: int = 2, encrypt_dir: bool = True) -> Path:
        path = self.dir / name
        writer = PCKWriter(
            path, format_version=version, engine_version=(4, 5, 0) if version == 3 else (4, 2, 0),
            encryption_key=KEY_HEX, encrypt_directory=encrypt_dir,
        )
        writer.add_bytes("res://scene.tscn", b'text = "Hi"\n', encrypted=True)
        writer.add_bytes("res://plain.txt", b"visible", encrypted=False)
        writer.add_bytes("res://empty.bin", b"", encrypted=True)
        writer.build()
        return path

    def test_roundtrip_v2_and_v3(self) -> None:
        for version in (2, 3):
            reader = PCKReader(self._build(f"v{version}.pck", version), key=KEY_HEX)
            reader.open()
            self.assertTrue(reader.dir_encrypted)
            self.assertEqual(reader.read_file_bytes("res://scene.tscn", verify_md5=True), b'text = "Hi"\n')
            self.assertEqual(reader.read_file_bytes("res://plain.txt"), b"visible")
            self.assertEqual(reader.read_file_bytes("res://empty.bin"), b"")
            self.assertEqual(reader.encrypted_file_count, 2)

    def test_header_and_ciphertext_on_disk(self) -> None:
        blob = self._build("v2.pck").read_bytes()
        self.assertTrue(struct.unpack_from("<I", blob, 20)[0] & 1)  # DIR_ENCRYPTED
        self.assertNotIn(b'text = "Hi"', blob)  # payload really is ciphertext
        self.assertNotIn(b"res://scene.tscn", blob)  # and so is the directory
        self.assertIn(b"visible", blob)  # unencrypted entries stay readable

    def test_repack_keeps_encryption(self) -> None:
        source = self._build("src.pck")
        out = self.dir / "out.pck"
        RepackPatcher.repack(source, out, {"res://scene.tscn": b'text = "Selam"\n'}, key=KEY_HEX)
        reader = PCKReader(out, key=KEY_HEX)
        reader.open()
        self.assertTrue(reader.dir_encrypted)
        self.assertTrue(reader.entries["res://scene.tscn"].is_encrypted)
        self.assertFalse(reader.entries["res://plain.txt"].is_encrypted)
        self.assertEqual(reader.read_file_bytes("res://scene.tscn"), b'text = "Selam"\n')

    def test_writer_validation(self) -> None:
        with self.assertRaises(PCKEncryptionError):
            PCKWriter(self.dir / "a.pck", encrypt_directory=True)
        with self.assertRaises(PCKEncryptionError):
            PCKWriter(self.dir / "b.pck").add_bytes("res://x", b"x", encrypted=True)
        with self.assertRaises(PCKEncryptionError):
            PCKWriter(self.dir / "c.pck", format_version=1, encryption_key=KEY_HEX)


class TestKeyHandling(unittest.TestCase):
    def test_parse_key(self) -> None:
        self.assertEqual(parse_key(KEY_HEX.upper()), bytes.fromhex(KEY_HEX))
        self.assertEqual(parse_key("  0x" + KEY_HEX + " "), bytes.fromhex(KEY_HEX))
        self.assertIsNone(parse_key(""))
        self.assertIsNone(parse_key(None))
        for bad in ("zz" * 32, "ab" * 16, "ab" * 33):
            with self.assertRaises(PCKEncryptionError):
                parse_key(bad)

    def test_keys_are_masked_in_logs(self) -> None:
        self.assertNotIn(KEY_HEX, mask_secrets(f"key={KEY_HEX} failed"))


if __name__ == "__main__":
    unittest.main()
