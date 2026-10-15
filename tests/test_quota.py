import asyncio
from datetime import datetime

import pytest

from app.extraction import extractor, llm
from app.extraction.quota import (
    QUOTA_TZ, LLMBadOutput, LLMUnavailable, LRUCache, QuotaExceeded, RateLimiter, content_hash
)
from app.models.schemas import ExtractedResume


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self):
        return self.t


def _at(y, m, d, hh, mm=0, ss=0) -> float:
    return datetime(y, m, d, hh, mm, ss, tzinfo=QUOTA_TZ).timestamp()


# ─────────────────────────── rate limiter ───────────────────────────

def test_rpm_window_blocks_then_frees():
    clock = Clock(_at(2026, 10, 15, 9))
    limiter = RateLimiter(rpm=2, rpd=100, clock=clock)
    limiter.acquire()
    limiter.acquire()
    with pytest.raises(QuotaExceeded) as e:
        limiter.acquire()
    assert e.value.reason == "rpm"
    assert 1 <= e.value.retry_after <= 60
    clock.t += 61
    limiter.acquire()  # window slid


def test_daily_budget_resets_at_pacific_midnight():
    clock = Clock(_at(2026, 10, 15, 23, 59, 0))
    limiter = RateLimiter(rpm=100, rpd=2, clock=clock)
    limiter.acquire()
    limiter.acquire()
    with pytest.raises(QuotaExceeded) as e:
        limiter.acquire()
    assert e.value.reason == "rpd"
    assert e.value.retry_after == 60          # one minute to midnight Pacific
    clock.t += 61
    limiter.acquire()                          # new Pacific day


def test_explanations_stop_before_parsing_does():
    clock = Clock(_at(2026, 10, 15, 9))
    limiter = RateLimiter(rpm=100, rpd=10, explain_ceiling=0.8, clock=clock)
    for _ in range(8):
        limiter.acquire("parse")
    with pytest.raises(QuotaExceeded) as e:
        limiter.acquire("explain")
    assert e.value.reason == "explain_budget"
    limiter.acquire("parse")                   # the reserved 20% is still usable for parsing
    limiter.acquire("parse")
    with pytest.raises(QuotaExceeded):
        limiter.acquire("parse")


def test_cooldown_after_gemini_429():
    clock = Clock(_at(2026, 10, 15, 9))
    limiter = RateLimiter(rpm=100, rpd=100, clock=clock)
    limiter.cooldown(30)
    with pytest.raises(QuotaExceeded) as e:
        limiter.acquire()
    assert e.value.reason == "gemini_cooldown"
    assert e.value.retry_after == 30
    clock.t += 31
    limiter.acquire()


# ─────────────────────────── cache + hashing ───────────────────────────

def test_hash_ignores_whitespace_but_not_content():
    a = content_hash("resume", "Python  and\n\nPyTorch ", "2", "m")
    b = content_hash("resume", "Python and PyTorch", "2", "m")
    c = content_hash("resume", "Python and JAX", "2", "m")
    d = content_hash("jd", "Python and PyTorch", "2", "m")
    e = content_hash("resume", "Python and PyTorch", "3", "m")
    assert a == b
    assert len({b, c, d, e}) == 4  # content, kind and prompt version all matter


def test_lru_cache_evicts_oldest():
    cache = LRUCache(max_entries=2)
    cache.set("a", {"v": 1})
    cache.set("b", {"v": 2})
    cache.get("a")                 # a is now most recent
    cache.set("c", {"v": 3})
    assert cache.get("b") is None
    assert cache.get("a") == {"v": 1}


# ─────────────────────────── Gemini call handling ───────────────────────────

class FakeAPIError(llm.genai_errors.APIError):
    def __init__(self, code, details=None):
        Exception.__init__(self, f"{code}")
        self.code = code
        self.details = details or {}


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return type("Resp", (), {"text": outcome})()


@pytest.fixture
def fake_gemini(monkeypatch):
    def install(outcomes, rpm=100, rpd=100):
        models = FakeModels(outcomes)
        monkeypatch.setattr(llm, "get_client", lambda: type("C", (), {"models": models})())
        limiter = RateLimiter(rpm=rpm, rpd=rpd)
        monkeypatch.setattr(llm, "get_limiter", lambda: limiter)
        monkeypatch.setattr(llm, "_SERVER_RETRY_DELAY_S", 0)
        return models, limiter
    return install


def test_gemini_429_sets_cooldown_and_raises_without_retry(fake_gemini):
    details = {"error": {"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                      "retryDelay": "34s"}]}}
    models, limiter = fake_gemini([FakeAPIError(429, details)])
    with pytest.raises(QuotaExceeded) as e:
        asyncio.run(llm.generate_text("p"))
    assert e.value.retry_after == 34
    assert models.calls == 1
    with pytest.raises(QuotaExceeded):        # cooldown blocks the next call locally
        limiter.acquire()


def test_gemini_5xx_retries_once_then_reports_unavailable(fake_gemini):
    models, _ = fake_gemini([FakeAPIError(503), FakeAPIError(503)])
    with pytest.raises(LLMUnavailable):
        asyncio.run(llm.generate_text("p"))
    assert models.calls == 2

    models, _ = fake_gemini([FakeAPIError(500), "ok"])
    assert asyncio.run(llm.generate_text("p")) == "ok"


def test_invalid_json_retries_once_then_bad_output(fake_gemini):
    models, _ = fake_gemini(["not json", '{"name": "Ada", "skills": []}'])
    result = asyncio.run(llm.generate_model("p", ExtractedResume))
    assert result.name == "Ada"
    assert models.calls == 2

    models, _ = fake_gemini(["nope", "still nope"])
    with pytest.raises(LLMBadOutput):
        asyncio.run(llm.generate_model("p", ExtractedResume))


def test_identical_text_is_served_from_cache(fake_gemini, monkeypatch):
    models, _ = fake_gemini(['{"name": "Ada", "skills": []}'])
    monkeypatch.setattr(extractor, "get_cache", lambda cache=LRUCache(): cache)

    first, source1 = asyncio.run(extractor.extract_resume("Ada. Python."))
    second, source2 = asyncio.run(extractor.extract_resume("Ada.   Python.  "))
    assert (source1, source2) == ("llm", "cache")
    assert first == second
    assert models.calls == 1
