"""While Google throttles the IP, bulk translation must switch routes instead of sleeping out cooldowns."""

import unittest

from src.core.translators.base import BaseTranslator, TranslationEngine, TranslationResult
from src.core.translators.google import GoogleTranslator
from src.core.translators.google_transport import PRIMARY_CLIENTS, RateLimitedError, TransportError
from src.core.translators.network import NetworkSettings
from src.core.translators.router import EndpointRouter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.now += seconds


class BlockedPrimaryTransport:
    """Every /translate_a/single call is throttled; batchexecute and clients5 work."""

    def __init__(self) -> None:
        self.primary_calls = 0
        self.batch_calls = 0

    def primary(self, endpoint: str, text: str, sl: str, tl: str, client: str = "gtx") -> str:
        self.primary_calls += 1
        raise RateLimitedError(endpoint)

    def batchexecute(self, texts: list[str], sl: str, tl: str) -> list[str]:
        self.batch_calls += 1
        return [f"tr:{t}" for t in texts]

    def clients5(self, text: str, sl: str, tl: str) -> str:
        return f"tr:{text}"

    def close(self) -> None:
        pass


class TestGoogleRouting(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.router = EndpointRouter(clock=self.clock, sleep=self.clock.sleep)
        self.transport = BlockedPrimaryTransport()
        self.google = GoogleTranslator(settings=NetworkSettings(), router=self.router, transport=self.transport)

    def test_batches_fall_through_to_batchexecute_without_waiting(self) -> None:
        texts = [f"line {i} " + "x" * 400 for i in range(60)]  # > 8000 chars: several slices
        results = self.google.translate_batch(texts, "en", "tr")
        self.assertTrue(all(r.success for r in results))
        self.assertEqual(results[0].translated_text, f"tr:{texts[0]}")
        self.assertEqual(self.clock.slept, 0.0)
        self.assertGreaterEqual(self.transport.batch_calls, 2)

    def test_single_lines_skip_primary_during_cooldown(self) -> None:
        self.router.on_rate_limited()
        calls_before = self.transport.primary_calls
        result = self.google.translate("Hello", "en", "tr")
        self.assertTrue(result.success)
        self.assertEqual(self.transport.primary_calls, calls_before)
        self.assertEqual(self.clock.slept, 0.0)


class ClientGatedTransport:
    """Google refuses ``client=gtx`` (regional block) but serves the other client ids."""

    def __init__(self, working: tuple[str, ...]) -> None:
        self.working = working
        self.clients_seen: list[str] = []

    def primary(self, endpoint: str, text: str, sl: str, tl: str, client: str = "gtx") -> str:
        self.clients_seen.append(client)
        if client not in self.working:
            raise RateLimitedError(endpoint)
        return f"tr:{text}"

    def clients5(self, text: str, sl: str, tl: str) -> str:
        raise TransportError("clients5 disabled")

    def batchexecute(self, texts: list[str], sl: str, tl: str) -> list[str] | None:
        return None

    def lingva(self, text: str, sl: str, tl: str) -> str:
        raise TransportError("lingva disabled")

    def close(self) -> None:
        pass


class TestClientRotation(unittest.TestCase):
    def _google(self, working: tuple[str, ...]) -> tuple[GoogleTranslator, ClientGatedTransport, EndpointRouter]:
        router = EndpointRouter(endpoints=["https://example.test/translate_a/single"])
        transport = ClientGatedTransport(working)
        return GoogleTranslator(NetworkSettings(), router, transport), transport, router

    def test_gtx_block_is_bypassed_and_remembered(self) -> None:
        google, transport, router = self._google(("at",))
        self.assertEqual(google.translate("Hello", "en", "tr").translated_text, "tr:Hello")
        self.assertEqual(transport.clients_seen, ["gtx", "at"])
        transport.clients_seen.clear()
        google.translate("World", "en", "tr")
        self.assertEqual(transport.clients_seen, ["at"])  # the working id goes first now
        self.assertFalse(router.in_cooldown)

    def test_all_clients_refused_counts_as_ip_block(self) -> None:
        google, transport, router = self._google(())
        google.translate("Hello", "en", "tr")
        self.assertTrue(set(transport.clients_seen) >= set(PRIMARY_CLIENTS))
        self.assertTrue(router.in_cooldown)


class FakeFallback(BaseTranslator):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[tuple[list[str], str, str]] = []

    def translate(self, text: str, source_lang: str = "auto", target_lang: str = "tr") -> TranslationResult:
        return self.translate_batch([text], source_lang, target_lang)[0]

    def translate_batch(self, texts: list[str], source_lang: str = "auto", target_lang: str = "tr") -> list[TranslationResult]:
        self.seen.append((list(texts), source_lang, target_lang))
        return [
            TranslationResult(t, f"bing:{t}", source_lang, target_lang, TranslationEngine.BING, True)
            for t in texts
        ]


class AllBlockedTransport(ClientGatedTransport):
    def clients5(self, text: str, sl: str, tl: str) -> str:
        raise RateLimitedError("clients5")


class TestGoogleBingFallback(unittest.TestCase):
    def test_lines_google_refused_go_to_bing(self) -> None:
        router = EndpointRouter(endpoints=["https://example.test/translate_a/single"])
        google = GoogleTranslator(NetworkSettings(max_retries=1), router, AllBlockedTransport(()))
        fallback = FakeFallback()
        google.set_fallback(fallback)
        results = google.translate_batch(["Hello", "World"], "en", "pt")
        self.assertTrue(all(r.success for r in results))
        self.assertEqual(results[0].translated_text, "bing:Hello")
        self.assertEqual(fallback.seen[0][0], ["Hello", "World"])

    def test_without_fallback_failures_are_reported(self) -> None:
        router = EndpointRouter(endpoints=["https://example.test/translate_a/single"])
        google = GoogleTranslator(NetworkSettings(max_retries=1), router, AllBlockedTransport(()))
        self.assertFalse(google.translate_batch(["Hello"], "en", "tr")[0].success)


if __name__ == "__main__":
    unittest.main()
