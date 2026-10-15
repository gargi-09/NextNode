"""Gemini free-tier protection: a global rate limiter and a content-hash cache.

Both are in-memory (per process / per Lambda container) behind small interfaces,
so a Redis-backed implementation can replace them later without touching callers.
The Gemini quota is per Google Cloud project, so these limits are global — every
user and tenant draws from the same budget.
"""
import hashlib
import math
import re
import threading
import time
import unicodedata
from collections import OrderedDict, deque
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

# Gemini's requests-per-day quota resets at midnight Pacific time
QUOTA_TZ = ZoneInfo("America/Los_Angeles")


class QuotaExceeded(Exception):
    """The local budget or Gemini itself refused the call. Maps to HTTP 429."""
    def __init__(self, retry_after: int, reason: str):
        super().__init__(f"LLM quota exceeded ({reason}); retry after {retry_after}s")
        self.retry_after = max(1, int(retry_after))
        self.reason = reason


class LLMUnavailable(Exception):
    """Gemini failed with a server-side or network error. Maps to HTTP 503."""


class LLMBadOutput(Exception):
    """Gemini returned output that is not valid for the expected schema. Maps to HTTP 502."""


class RateLimiter:
    """Sliding one-minute window + Pacific-day counter + cooldown after a Gemini 429.

    kind="explain" calls stop once the day reaches `explain_ceiling` of the budget,
    keeping the remainder for parsing, which the product cannot work without.
    """
    def __init__(self, rpm: int, rpd: int, explain_ceiling: float = 0.8,
                 clock: Callable[[], float] = time.time):
        self.rpm = rpm
        self.rpd = rpd
        self.explain_ceiling = explain_ceiling
        self._clock = clock
        self._lock = threading.Lock()
        self._minute: deque[float] = deque()
        self._day: str | None = None
        self._day_count = 0
        self._cooldown_until = 0.0

    def _pacific_day(self, now: float) -> str:
        return datetime.fromtimestamp(now, QUOTA_TZ).date().isoformat()

    def _seconds_to_pacific_midnight(self, now: float) -> int:
        local = datetime.fromtimestamp(now, QUOTA_TZ)
        midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return math.ceil(midnight.timestamp() - now)

    def acquire(self, kind: str = "parse") -> None:
        """Reserve one request or raise QuotaExceeded. Never sleeps."""
        with self._lock:
            now = self._clock()

            if now < self._cooldown_until:
                raise QuotaExceeded(math.ceil(self._cooldown_until - now), "gemini_cooldown")

            day = self._pacific_day(now)
            if day != self._day:
                self._day, self._day_count = day, 0

            while self._minute and now - self._minute[0] >= 60:
                self._minute.popleft()
            if len(self._minute) >= self.rpm:
                raise QuotaExceeded(math.ceil(60 - (now - self._minute[0])), "rpm")

            if self._day_count >= self.rpd:
                raise QuotaExceeded(self._seconds_to_pacific_midnight(now), "rpd")
            if kind == "explain" and self._day_count >= self.rpd * self.explain_ceiling:
                raise QuotaExceeded(self._seconds_to_pacific_midnight(now), "explain_budget")

            # reserve before the call and never refund: our count can't fall behind Google's
            self._minute.append(now)
            self._day_count += 1

    def cooldown(self, seconds: int) -> None:
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, self._clock() + seconds)

    def usage(self) -> dict:
        with self._lock:
            now = self._clock()
            today = self._pacific_day(now)
            used = self._day_count if self._day == today else 0
            return {"rpd_limit": self.rpd, "rpd_used": used,
                    "rpm_limit": self.rpm,
                    "rpm_used": sum(1 for t in self._minute if now - t < 60),
                    "cooldown_s": max(0, math.ceil(self._cooldown_until - now))}


class LRUCache:
    """Small thread-safe LRU for validated LLM extractions, keyed by content hash."""
    def __init__(self, max_entries: int = 512):
        self.max_entries = max_entries
        self._data: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> dict | None:
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def set(self, key: str, value: dict) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)


_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Whitespace- and Unicode-insensitive form used for hashing only."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def content_hash(kind: str, text: str, prompt_version: str, model: str) -> str:
    payload = f"{kind}|{prompt_version}|{model}|{normalize_text(text)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
