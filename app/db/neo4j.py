"""Neo4j access for Aura Free: lazy driver, short timeouts, and a circuit breaker.

All graph work goes through `neo4j_client.read(tx_fn, ...)` / `.write(tx_fn, ...)`:
  * managed transactions — the driver retries transient errors and stale connections
    for up to `neo4j_max_retry_time_s`
  * connectivity failures (paused instance, network) raise GraphUnavailableError,
    which the API returns as 503 + Retry-After + an admin hint, never a crash
  * after `breaker_failure_threshold` such failures within `breaker_window_s`, the
    breaker opens and calls fail fast for `breaker_cooldown_s` without touching the
    network; then a single probe call decides whether to close it again
Domain errors raised inside a transaction (NotFoundError, ConflictError) pass through
untouched and never count as failures.
"""
import threading
import time
from typing import Callable

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, SessionExpired

from app.core.config import settings

PAUSED_HINT = (
    "The graph database is unreachable. Aura Free pauses after about 3 days of inactivity: "
    "resume the instance at https://console.neo4j.io (Instances → Resume). "
    "Instances left paused for 30 days are deleted."
)

_CONNECTIVITY_ERRORS = (ServiceUnavailable, SessionExpired, OSError)


class GraphUnavailableError(Exception):
    def __init__(self, retry_after: int, hint: str = PAUSED_HINT):
        super().__init__("Graph database unavailable")
        self.retry_after = max(1, int(retry_after))
        self.hint = hint


class CircuitBreaker:
    def __init__(self, threshold: int, window_s: float, cooldown_s: float,
                 clock: Callable[[], float] = time.monotonic):
        self.threshold = threshold
        self.window_s = window_s
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: list[float] = []
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state(self._clock())

    def _state(self, now: float) -> str:
        if self._opened_at is None:
            return "closed"
        return "open" if now - self._opened_at < self.cooldown_s else "half_open"

    def before_call(self) -> None:
        with self._lock:
            now = self._clock()
            state = self._state(now)
            if state == "open":
                raise GraphUnavailableError(self.cooldown_s - (now - self._opened_at))
            if state == "half_open":
                if self._probe_in_flight:
                    raise GraphUnavailableError(self.cooldown_s)
                self._probe_in_flight = True  # exactly one probe goes through

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()
            self._opened_at = None
            self._probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            now = self._clock()
            if self._probe_in_flight:  # probe failed: stay open for another cooldown
                self._probe_in_flight = False
                self._opened_at = now
                return
            self._failures = [t for t in self._failures if now - t < self.window_s] + [now]
            if len(self._failures) >= self.threshold:
                self._opened_at = now

    def retry_after(self) -> int:
        with self._lock:
            now = self._clock()
            if self._opened_at is None:
                return int(self.cooldown_s)
            return max(1, int(self.cooldown_s - (now - self._opened_at)))


class Neo4jClient:
    def __init__(self):
        self._driver = None
        self._driver_lock = threading.Lock()
        self.breaker = CircuitBreaker(
            settings.breaker_failure_threshold,
            settings.breaker_window_s,
            settings.breaker_cooldown_s,
        )

    @property
    def driver(self):
        # created on first use, then reused by every request in this process / Lambda container
        if self._driver is None:
            with self._driver_lock:
                if self._driver is None:
                    self._driver = GraphDatabase.driver(
                        settings.neo4j_uri,
                        auth=(settings.neo4j_username, settings.neo4j_password),
                        connection_timeout=settings.neo4j_connection_timeout_s,
                        connection_acquisition_timeout=settings.neo4j_acquisition_timeout_s,
                        max_transaction_retry_time=settings.neo4j_max_retry_time_s,
                        # pooled connections idle longer than this are checked before
                        # reuse — essential after a Lambda freeze or an Aura idle drop
                        liveness_check_timeout=settings.neo4j_liveness_check_timeout_s,
                        max_connection_lifetime=settings.neo4j_max_connection_lifetime_s,
                        max_connection_pool_size=settings.neo4j_pool_size,
                        keep_alive=True,
                    )
        return self._driver

    def close(self):
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def get_session(self):
        """Raw session for scripts; request paths should use read()/write()."""
        return self.driver.session(database=settings.neo4j_database)

    def read(self, tx_fn, *args):
        return self._run("execute_read", tx_fn, *args)

    def write(self, tx_fn, *args):
        return self._run("execute_write", tx_fn, *args)

    def _run(self, mode: str, tx_fn, *args):
        self.breaker.before_call()
        try:
            with self.get_session() as session:
                result = getattr(session, mode)(tx_fn, *args)
        except _CONNECTIVITY_ERRORS as e:
            self.breaker.record_failure()
            raise GraphUnavailableError(self.breaker.retry_after()) from e
        except BaseException:
            # the database answered (domain error, Cypher error): connectivity is fine
            self.breaker.record_success()
            raise
        self.breaker.record_success()
        return result

    def health(self) -> dict:
        """Connectivity probe plus a few counts for /health/graph."""
        try:
            counts = self.read(_health_tx)
            return {"graph": "ok", "breaker": self.breaker.state, **counts}
        except GraphUnavailableError as e:
            return {"graph": "unavailable", "breaker": self.breaker.state,
                    "retry_after": e.retry_after, "hint": e.hint}


def _health_tx(tx) -> dict:
    record = tx.run("""
        OPTIONAL MATCH (n) WITH count(n) AS nodes
        OPTIONAL MATCH ()-[r]->() WITH nodes, count(r) AS relationships
        OPTIONAL MATCH (s:Skill) WHERE s.key IS NOT NULL
        RETURN nodes, relationships, count(s) AS canonical_skills
    """).single()
    return dict(record)


neo4j_client = Neo4jClient()
