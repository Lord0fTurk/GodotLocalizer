"""API keys and the PCK key live in the OS credential store when one exists (fake store used here)."""

import json
import tempfile
import unittest
from pathlib import Path

from src.utils.config import KEYRING_SERVICE, SECRET_KEYS, ConfigManager


class FakeVault:
    def __init__(self, fail_writes: bool = False) -> None:
        self.items: dict[tuple[str, str], str] = {}
        self.fail_writes = fail_writes

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.fail_writes:
            raise RuntimeError("no backend")
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.items:
            raise KeyError(username)
        del self.items[(service, username)]


class TestConfigSecrets(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "config.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def on_disk(self) -> dict[str, object]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_secrets_go_to_the_vault_not_the_file(self) -> None:
        vault = FakeVault()
        config = ConfigManager(self.path, vault=vault)
        config.set("openai_key", "sk-secret-value")
        config.set("pck_encryption_key", "ab" * 32)
        config.set("target_language", "de")
        self.assertEqual(config.get("openai_key"), "sk-secret-value")
        self.assertEqual(config.to_dict()["pck_encryption_key"], "ab" * 32)
        self.assertEqual(vault.items[(KEYRING_SERVICE, "openai_key")], "sk-secret-value")
        stored = self.on_disk()
        self.assertEqual((stored["openai_key"], stored["pck_encryption_key"], stored["target_language"]), ("", "", "de"))
        self.assertNotIn("sk-secret-value", self.path.read_text(encoding="utf-8"))

    def test_plaintext_keys_from_an_old_file_are_migrated(self) -> None:
        self.path.write_text(json.dumps({"gemini_key": "AIza-old-plain-key", "deepl_key": ""}), encoding="utf-8")
        vault = FakeVault()
        config = ConfigManager(self.path, vault=vault)
        self.assertEqual(config.get("gemini_key"), "AIza-old-plain-key")
        self.assertEqual(self.on_disk()["gemini_key"], "")
        self.assertEqual(ConfigManager(self.path, vault=vault).get("gemini_key"), "AIza-old-plain-key")

    def test_clearing_a_key_removes_it_from_the_vault(self) -> None:
        vault = FakeVault()
        config = ConfigManager(self.path, vault=vault)
        config.set("openai_key", "sk-x")
        config.set("openai_key", "")
        self.assertEqual(config.get("openai_key"), "")
        self.assertNotIn((KEYRING_SERVICE, "openai_key"), vault.items)

    def test_without_a_vault_everything_stays_in_the_file(self) -> None:
        config = ConfigManager(self.path, vault=None)
        config.set("openai_key", "sk-plain")
        self.assertEqual(self.on_disk()["openai_key"], "sk-plain")
        self.assertEqual(config.get("openai_key"), "sk-plain")

    def test_a_failing_vault_falls_back_to_the_file_instead_of_losing_the_key(self) -> None:
        config = ConfigManager(self.path, vault=FakeVault(fail_writes=True))
        config.set("deepseek_key", "sk-fallback")
        self.assertEqual(self.on_disk()["deepseek_key"], "sk-fallback")
        self.assertEqual(config.get("deepseek_key"), "sk-fallback")

    def test_update_routes_secrets_too_and_the_key_list_is_complete(self) -> None:
        vault = FakeVault()
        config = ConfigManager(self.path, vault=vault)
        config.update({"libretranslate_key": "lt-key", "batch_size": 50})
        self.assertEqual(vault.items[(KEYRING_SERVICE, "libretranslate_key")], "lt-key")
        self.assertEqual(config.get("batch_size"), 50)
        self.assertTrue({"openai_key", "gemini_key", "deepseek_key", "deepl_key", "libretranslate_key"} <= SECRET_KEYS)


if __name__ == "__main__":
    unittest.main()
