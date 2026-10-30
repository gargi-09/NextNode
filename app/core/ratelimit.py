"""Per-client rate limit for the public analyze endpoint.

In-process sliding windows (per minute and per day) keyed by client IP. Like the Gemini limiter
it is per process, which is why a public deployment runs a single instance. The Gemini limiter
still applies underneath: this one stops one visitor from spending everyone's daily budget.
"""
import math
import threading
import time
from collections import deque

MINUTE, DAY = 60.0, 86_400.0


class ClientRateLimiter:
    def __init__(self, per_minute: int, per_day: int, max_clients: int = 10_000, clock=time.monotonic):
        self.per_minute, self.per_day, self.max_clients, self.clock = per_minute, per_day, max_clients, clock
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def acquire(self, client: str) -> int | None:
        """Record a request. Returns None if allowed, else the seconds to wait (Retry-After)."""
        now = self.clock()
        with self._lock:
            hits = self._hits.get(client)
            if hits is None:
                if len(self._hits) >= self.max_clients:      # forget the least recently seen client
                    oldest = min(self._hits, key=lambda k: self._hits[k][-1] if self._hits[k] else 0)
                    del self._hits[oldest]
                hits = self._hits[client] = deque()
            while hits and now - hits[0] >= DAY:
                hits.popleft()
            if len(hits) >= self.per_day:
                return math.ceil(DAY - (now - hits[0]))
            recent = [t for t in hits if now - t < MINUTE]
            if len(recent) >= self.per_minute:
                return math.ceil(MINUTE - (now - recent[0]))
            hits.append(now)
            return None
