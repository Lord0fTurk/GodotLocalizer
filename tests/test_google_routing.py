"""While Google throttles the IP, bulk translation must switch routes instead of sleeping out cooldowns."""

import unittest

from src.core.translators.google import GoogleTranslator
from src.core.translators.google_transport import RateLimitedError
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

    def primary(self, endpoint: str, text: str, sl: str, tl: str) -> str:
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


if __name__ == "__main__":
    unittest.main()
