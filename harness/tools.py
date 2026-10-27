"""Every MCP tool call goes through here: timeout, retry with backoff, a breaker per tool.

    ToolRunner(call).call(name, arguments) -> (ok, text)     # same shape as an MCP call

Errors are classified from the MCP server's coded JSON (app/mcp_server.py):
  transient  GRAPH_UNAVAILABLE, INTERNAL, a timeout     -> retried with exponential backoff + jitter
  permanent  NOT_FOUND, INVALID_INPUT, UNPROCESSABLE, CONFLICT, CIRCUIT_OPEN -> returned at once
Each tool has its own breaker: 3 consecutive failed calls (after retries) open it for 30 s,
then one probe call is let through. Another tool keeps working while one is open.
"""
import asyncio
import json
import random
import time
from dataclasses import dataclass, field

TRANSIENT = {"GRAPH_UNAVAILABLE", "INTERNAL", "TIMEOUT"}


def error_code(text: str) -> str | None:
    """The NextNode error code in a tool error text, e.g. 'GRAPH_UNAVAILABLE', or None."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return obj.get("code") if isinstance(obj, dict) else None


def coded(code: str, message: str) -> str:
    return json.dumps({"code": code, "message": message})


class Breaker:
    """closed -> (threshold consecutive failures) -> open -> (cooldown) -> half_open -> one probe."""

    def __init__(self, threshold: int = 3, cooldown_s: float = 30.0, clock=time.monotonic):
        self.threshold, self.cooldown_s, self.clock = threshold, cooldown_s, clock
        self.failures, self.opened_at, self.opens = 0, None, 0

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "open" if self.clock() - self.opened_at < self.cooldown_s else "half_open"

    def allow(self) -> bool:
        return self.state != "open"

    def success(self):
        self.failures, self.opened_at = 0, None

    def failure(self):
        self.failures += 1
        if self.state == "half_open" or self.failures >= self.threshold:
            if self.opened_at is None or self.state == "half_open":
                self.opens += 1
            self.opened_at = self.clock()


@dataclass
class ToolStats:
    calls: int = 0
    attempts: int = 0
    retries: int = 0
    recovered: int = 0          # calls that failed at first and then succeeded
    failed: int = 0
    breaker_opens: int = 0
    by_code: dict = field(default_factory=dict)


class ToolRunner:
    def __init__(self, call, *, max_attempts: int = 3, timeout_s: float = 60.0, base_delay_s: float = 0.5,
                 breaker_threshold: int = 3, breaker_cooldown_s: float = 30.0, faults=None, tracer=None,
                 sleep=asyncio.sleep, clock=time.monotonic, rng=random.random):
        self._call, self.max_attempts, self.timeout_s, self.base_delay_s = call, max_attempts, timeout_s, base_delay_s
        self._breaker_args = (breaker_threshold, breaker_cooldown_s, clock)
        self.breakers: dict[str, Breaker] = {}
        self.faults, self.tracer, self._sleep, self._rng = faults, tracer, sleep, rng
        self.stats = ToolStats()

    def breaker(self, name: str) -> Breaker:
        return self.breakers.setdefault(name, Breaker(*self._breaker_args))

    def backoff(self, attempt: int) -> float:
        """Delay before retry number `attempt` (1-based): base * 2^(attempt-1), plus up to 50% jitter."""
        base = self.base_delay_s * 2 ** (attempt - 1)
        return base + base * 0.5 * self._rng()

    async def _attempt(self, name: str, arguments: dict) -> tuple[bool, str]:
        injected = self.faults.tool_fault(name) if self.faults else None
        if injected == "TIMEOUT":
            return False, coded("TIMEOUT", f"{name} timed out (injected)")
        if injected:
            return False, coded(injected, f"{name} failed with {injected} (injected)")
        try:
            return await asyncio.wait_for(self._call(name, arguments), self.timeout_s)
        except asyncio.TimeoutError:
            return False, coded("TIMEOUT", f"{name} timed out after {self.timeout_s:.0f}s")

    async def call(self, name: str, arguments: dict) -> tuple[bool, str]:
        self.stats.calls += 1
        breaker = self.breaker(name)
        span = self.tracer.span("tool_call", {"tool.name": name}) if self.tracer else None
        with span if span else _null():
            if not breaker.allow():
                self._count("CIRCUIT_OPEN")
                self.stats.failed += 1
                _set(span, attempts=0, outcome="circuit_open")
                return False, coded("CIRCUIT_OPEN", f"{name} is failing; circuit open for "
                                                    f"{breaker.cooldown_s:.0f}s. Do not call it again now.")
            for attempt in range(1, self.max_attempts + 1):
                self.stats.attempts += 1
                t = time.monotonic()
                ok, text = await self._attempt(name, arguments)
                code = None if ok else (error_code(text) or "UNKNOWN")
                if self.tracer:
                    self.tracer.event(span, "attempt", {"attempt": attempt, "outcome": "ok" if ok else code,
                                                        "latency_ms": round((time.monotonic() - t) * 1000, 1)})
                if ok:
                    breaker.success()
                    if attempt > 1:
                        self.stats.recovered += 1
                    _set(span, attempts=attempt, outcome="ok")
                    return True, text
                self._count(code)
                if code not in TRANSIENT or attempt == self.max_attempts:
                    break
                self.stats.retries += 1
                await self._sleep(self.backoff(attempt))
            opens_before = breaker.opens
            breaker.failure()
            self.stats.breaker_opens += breaker.opens - opens_before
            self.stats.failed += 1
            _set(span, attempts=attempt, outcome=code)
            return False, text

    def _count(self, code: str):
        self.stats.by_code[code] = self.stats.by_code.get(code, 0) + 1


def _set(span, **attrs):
    if span is not None:
        span.set(**attrs)


class _null:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False
