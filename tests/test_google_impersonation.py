"""The curl_cffi transport is optional: it must be used when asked for and never required."""

import unittest
from unittest import mock

from src.core.translators import google_transport
from src.core.translators.google_transport import GoogleTransport
from src.core.translators.network import NetworkSettings


class TestImpersonation(unittest.TestCase):
    def test_disabled_setting_uses_plain_requests(self) -> None:
        transport = GoogleTransport(NetworkSettings(browser_impersonation=False))
        self.assertFalse(transport.impersonating)
        self.assertIn("User-Agent", transport._headers())

    def test_missing_package_falls_back_to_requests(self) -> None:
        with mock.patch.object(google_transport, "curl_requests", None):
            transport = GoogleTransport(NetworkSettings(browser_impersonation=True))
        self.assertFalse(transport.impersonating)

    def test_user_agent_is_fixed_per_transport(self) -> None:
        transport = GoogleTransport(NetworkSettings(browser_impersonation=False))
        self.assertEqual({transport._headers()["User-Agent"] for _ in range(20)}, {transport._user_agent})

    @unittest.skipIf(google_transport.curl_requests is None, "curl_cffi not installed")
    def test_impersonation_drops_the_user_agent_and_uses_one_session_per_thread(self) -> None:
        transport = GoogleTransport(NetworkSettings(browser_impersonation=True))
        self.assertTrue(transport.impersonating)
        self.assertNotIn("User-Agent", transport._headers())
        self.assertIs(transport._curl_session(), transport._curl_session())
        transport.close()


if __name__ == "__main__":
    unittest.main()
