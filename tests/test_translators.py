"""Rate-limit parity tests for the translation stack (RenLocalizer behavior port).

Covers: manager/engine-level deduplication, soft-ban detection on empty Google
payloads, proxy-adaptive concurrency, slice character budgets, the 429 circuit
breaker with probe gating, cooldown jitter, and the configured runner batch size.
"""

import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.core.cache import TranslationCache
from src.core.glossary import GlossaryManager
from src.core.translators.base import BaseTranslator, TranslationEngine, TranslationResult
from src.core.translators.google import SEPARATOR, GoogleTranslator
from src.core.translators.google_transport import GoogleTransport, TransportError
from src.core.translators.manager import TranslationManager
from src.core.translators.network import NetworkSettings, ProxyPool
from src.core.translators.router import EndpointRouter

GOOGLE_ENDPOINT = "https://translate.test/translate_a/single"


class StubConfig:
    """Minimal ConfigManager stand-in exposing get(key, default)."""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self._values = values or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


class RecordingEngine(BaseTranslator):
    """Records the exact texts handed to translate_batch and echoes them tagged."""

    def __init__(self) -> None:
        super().__init__()
        self.seen_batches: list[list[str]] = []

    def translate(self, text: str, source_lang: str = "auto", target_lang: str = "tr") -> TranslationResult:
        return self._make(text, target_lang)

    def translate_batch(
        self, texts: list[str], source_lang: str = "auto", target_lang: str = "tr"
    ) -> list[TranslationResult]:
        self.seen_batches.append(list(texts))
        return [self._make(t, target_lang) for t in texts]

    @staticmethod
    def _make(text: str, target_lang: str) -> TranslationResult:
        return TranslationResult(
            original_text=text, translated_text=f"{text}->{target_lang}", source_lang="en",
            target_lang=target_lang, engine=TranslationEngine.GOOGLE, success=True,
        )


class FakeGoogleTransport:
    """In-memory stand-in for GoogleTransport with scriptable family behavior."""

    def __init__(self, primary_mode: str = "echo", batchexecute_ok: bool = True) -> None:
        self.primary_mode = primary_mode
        self.batchexecute_ok = batchexecute_ok
        self.primary_texts: list[str] = []
        self.batchexecute_batches: list[list[str]] = []

    def primary(self, endpoint: str, text: str, source_lang: str, target_lang: str, client: str = "gtx") -> str:
        self.primary_texts.append(text)
        if self.primary_mode == "soft_ban":
            raise TransportError("Empty payload (soft ban)")
        return f"TR:{text}"

    def clients5(self, text: str, source_lang: str, target_lang: str) -> str:
        raise TransportError("clients5 disabled in test")

    def batchexecute(self, texts: list[str], source_lang: str, target_lang: str) -> list[str] | None:
        self.batchexecute_batches.append(list(texts))
        if not self.batchexecute_ok:
            return None
        return [f"BE:{t}" for t in texts]

    def lingva(self, text: str, source_lang: str, target_lang: str) -> str:
        raise TransportError("lingva disabled in test")

    def close(self) -> None:
        pass


class FakeClock:
    """Deterministic monotonic clock with recorded sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestManagerDeduplication(unittest.TestCase):
    """The manager must translate each unique text once and fan results out."""

    def _make_manager(self, tmp_dir: str, engine: BaseTranslator) -> TranslationManager:
        config = StubConfig({"use_cache": False, "tm_enabled": False, "protected_terms": ""})
        manager = TranslationManager(
            config=config,
            cache=TranslationCache(db_path=Path(tmp_dir) / "cache.db"),
            glossary=GlossaryManager(glossary_path=Path(tmp_dir) / "glossary.json"),
        )
        manager._translators[TranslationEngine.GOOGLE] = engine
        return manager

    def test_duplicate_texts_hit_engine_once(self) -> None:
        """Repeated lines are sent to the engine as one unique list."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            engine = RecordingEngine()
            manager = self._make_manager(tmp_dir, engine)
            results = manager.translate_batch(
                ["a", "b", "a", "c", "b"], source_lang="en", target_lang="tr",
                engine_type=TranslationEngine.GOOGLE,
            )
            self.assertEqual(engine.seen_batches, [["a", "b", "c"]])
            self.assertEqual(len(results), 5)
            self.assertEqual(results[0].translated_text, results[2].translated_text)
            self.assertEqual(results[1].translated_text, results[4].translated_text)
            self.assertTrue(all(r.success for r in results))

    def test_fanned_out_results_are_independent_objects(self) -> None:
        """Copies must not share one mutable result (glossary mutates translated_text)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            engine = RecordingEngine()
            manager = self._make_manager(tmp_dir, engine)
            results = manager.translate_batch(
                ["dup", "dup"], source_lang="en", target_lang="tr", engine_type=TranslationEngine.GOOGLE,
            )
            self.assertIsNot(results[0], results[1])
            results[0].translated_text = "mutated"
            self.assertNotEqual(results[1].translated_text, "mutated")


class TestGoogleEngineDeduplication(unittest.TestCase):
    """The Google engine dedups internally so direct callers get the same relief."""

    def test_duplicates_collapse_into_one_separator_request(self) -> None:
        transport = FakeGoogleTransport()
        translator = GoogleTranslator(settings=NetworkSettings(), transport=transport)
        results = translator.translate_batch(["x", "y", "x"], "en", "tr")
        self.assertEqual(transport.primary_texts, [f"x{SEPARATOR}y"])
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].translated_text, "TR:x")
        self.assertEqual(results[2].translated_text, "TR:x")
        self.assertIsNot(results[0], results[2])

    def test_slices_respect_settings_char_budget(self) -> None:
        """max_chars_per_request tunes the slice size (hard cap stays 8000)."""
        translator = GoogleTranslator(
            settings=NetworkSettings(max_chars_per_request=600), transport=FakeGoogleTransport(),
        )
        bodies = ["z" * 100 for _ in range(10)]
        slices = translator._slices(bodies)
        self.assertEqual([len(s) for s in slices], [6, 4])
        for chunk in slices:
            self.assertLessEqual(sum(len(bodies[i]) for i in chunk), 600)


class TestSoftBanDetection(unittest.TestCase):
    """HTTP 200 with an empty payload is a soft ban, never a success."""

    def test_primary_empty_segments_raise(self) -> None:
        transport = GoogleTransport(NetworkSettings())

        class EmptyReply:
            status_code = 200

            @staticmethod
            def json() -> Any:
                return [[]]

        transport._request = lambda *args, **kwargs: EmptyReply()  # type: ignore[method-assign]
        with self.assertRaises(TransportError):
            transport.primary(GOOGLE_ENDPOINT, "hello", "en", "tr")

    def test_primary_null_segments_raise(self) -> None:
        transport = GoogleTransport(NetworkSettings())

        class NullReply:
            status_code = 200

            @staticmethod
            def json() -> Any:
                return [None]

        transport._request = lambda *args, **kwargs: NullReply()  # type: ignore[method-assign]
        with self.assertRaises(TransportError):
            transport.primary(GOOGLE_ENDPOINT, "hello", "en", "tr")

    def test_soft_ban_falls_through_to_batchexecute(self) -> None:
        """A soft-banned primary must not ship empty text; the batch route rescues it."""
        transport = FakeGoogleTransport(primary_mode="soft_ban")
        translator = GoogleTranslator(
            settings=NetworkSettings(max_retries=1),
            router=EndpointRouter(endpoints=[GOOGLE_ENDPOINT]),
            transport=transport,
        )
        results = translator.translate_batch(["hello", "world"], "en", "tr")
        self.assertEqual(len(transport.primary_texts), 1)
        self.assertEqual(transport.batchexecute_batches, [["hello", "world"]])
        self.assertTrue(all(r.success for r in results))
        self.assertEqual(results[0].translated_text, "BE:hello")


class TestProxyAdaptiveConcurrency(unittest.TestCase):
    """RenLocalizer tier table: bigger healthy pools allow more parallelism."""

    def test_tiers(self) -> None:
        self.assertEqual(ProxyPool(()).adaptive_concurrency(), 2)
        self.assertEqual(ProxyPool([f"http://p{i}" for i in range(1)]).adaptive_concurrency(), 2)
        self.assertEqual(ProxyPool([f"http://p{i}" for i in range(5)]).adaptive_concurrency(), 4)
        self.assertEqual(ProxyPool([f"http://p{i}" for i in range(10)]).adaptive_concurrency(), 8)
        self.assertEqual(ProxyPool([f"http://p{i}" for i in range(20)]).adaptive_concurrency(), 16)
        self.assertEqual(ProxyPool([f"http://p{i}" for i in range(50)]).adaptive_concurrency(), 32)

    def test_effective_concurrency_uses_pool_health(self) -> None:
        settings = NetworkSettings(concurrency=32)
        pool = ProxyPool([f"http://p{i}" for i in range(12)])
        self.assertEqual(settings.effective_concurrency(32, pool), 8)
        self.assertEqual(settings.effective_concurrency(32), 4)
        self.assertEqual(settings.effective_concurrency(32, ProxyPool(())), 4)
        self.assertEqual(NetworkSettings(concurrency=2).effective_concurrency(32, pool), 2)


class TestRouterRateLimitParity(unittest.TestCase):
    """Circuit breaker, probe gating and jittered cooldown behave as in RenLocalizer."""

    def _router(self, clock: FakeClock) -> EndpointRouter:
        return EndpointRouter(endpoints=[GOOGLE_ENDPOINT], clock=clock, sleep=clock.sleep)

    def test_breaker_trips_after_threshold_and_gates_probes(self) -> None:
        clock = FakeClock()
        router = self._router(clock)
        for _ in range(5):
            router.on_rate_limited()
        self.assertFalse(router.breaker_tripped)
        router.on_rate_limited()
        self.assertTrue(router.breaker_tripped)
        self.assertFalse(router.claim_primary_slot())
        clock.advance(301.0)
        self.assertTrue(router.claim_primary_slot())
        self.assertFalse(router.claim_primary_slot())

    def test_primary_success_clears_breaker(self) -> None:
        clock = FakeClock()
        router = self._router(clock)
        for _ in range(6):
            router.on_rate_limited()
        router.on_primary_success()
        self.assertFalse(router.breaker_tripped)
        self.assertTrue(router.claim_primary_slot())

    def test_cooldown_sleep_carries_jitter(self) -> None:
        clock = FakeClock()
        router = self._router(clock)
        wait = router.on_rate_limited()
        self.assertEqual(wait, 3.0)
        clock2 = FakeClock()
        clock2.now = 1000.0
        router2 = EndpointRouter(endpoints=[GOOGLE_ENDPOINT], clock=lambda: 1000.0, sleep=clock2.sleep)
        router2.on_rate_limited()
        router2.wait_for_cooldown()
        self.assertEqual(len(clock2.slept), 1)
        self.assertGreaterEqual(clock2.slept[0], 3.3)
        self.assertLessEqual(clock2.slept[0], 4.2)


class TestOrchestratorBatchSizeWiring(unittest.TestCase):
    """The UI batch_size setting must reach the runner (was silently ignored)."""

    def test_runner_receives_configured_batch_size(self) -> None:
        from src.core.pipeline.orchestrator import GodotTranslationPipeline

        class StubManager:
            config = StubConfig({"batch_size": 250})

        pipeline = GodotTranslationPipeline(translation_manager=StubManager())  # type: ignore[arg-type]
        self.assertEqual(pipeline.runner.batch_size, 250)


if __name__ == "__main__":
    unittest.main()
