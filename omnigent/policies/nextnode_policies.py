"""NextNode guardrail policies for the Omnigent orchestrator.

Loaded by the local Omnigent server via `policy_modules: [nextnode_policies]`
(see omnigent/server_config.yaml); the startup scripts put this directory on
PYTHONPATH. Deliberately dependency-free — events and responses are the plain
dicts of Omnigent's policy contract (omnigent.policies.schema, v0.18.0):

    event    = {"type": "request" | "llm_request" | "tool_call" | ...,
                "data": ..., "context": {"model": str, "usage": {...}},
                "session_state": {...}}
    response = {"result": "ALLOW" | "DENY" | "ASK", "reason": str,
                "state_updates": [{"key", "action": "set" | "increment" | ..., "value"}]}
    returning None abstains.

Why custom policies: Omnigent's builtin `cost_budget` caps USD spend, but free-tier
Gemini and local Ollama models are unpriced ($0), so a dollar cap can never fire.
The real free-tier limits are requests and tokens per day per Google Cloud
project, which is what `daily_llm_budget` enforces.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

Event = dict[str, Any]
Response = dict[str, Any] | None
Policy = Callable[[Event], Response]

# Gemini's requests-per-day quota resets at midnight Pacific time
QUOTA_TZ = ZoneInfo("America/Los_Angeles")

DEFAULT_STATE_DIR = Path(os.environ.get("NEXTNODE_OMNIGENT_STATE", Path.home() / ".nextnode"))

_ALLOW: Response = {"result": "ALLOW"}
_file_lock = threading.Lock()


def _pacific_day(now: datetime) -> str:
    return now.astimezone(QUOTA_TZ).date().isoformat()


def _session_tokens(event: Event) -> float:
    usage = (event.get("context") or {}).get("usage") or {}
    try:
        return float(usage.get("total_tokens") or 0)
    except (TypeError, ValueError):
        return 0.0


def _model(event: Event) -> str:
    model = (event.get("context") or {}).get("model")
    return model if isinstance(model, str) else ""


class _DailyLedger:
    """Day-scoped counters in a small JSON file, shared by every session on
    this machine. session_state is per session, but the Gemini quota is per
    project per day, so the count has to outlive any one session."""

    def __init__(self, path: Path, clock: Callable[[], datetime]):
        self.path = path
        self.clock = clock

    def _read(self, today: str) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        if data.get("day") != today:
            data = {"day": today, "requests": 0, "tokens": 0}
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic on the same filesystem

    def check_and_record(self, add_requests: int, add_tokens: float,
                         max_requests: int, max_tokens: int) -> tuple[bool, dict]:
        """Adds this call's usage, then reports whether the day is now over budget.
        Tokens are only known after a call, so they are recorded on the NEXT check."""
        with _file_lock:
            data = self._read(_pacific_day(self.clock()))
            data["tokens"] = int(data["tokens"] + max(0.0, add_tokens))
            over = data["requests"] >= max_requests or data["tokens"] >= max_tokens
            if not over:
                data["requests"] += add_requests
            self._write(data)
            return over, data


def daily_llm_budget(
    max_requests_per_day: int = 150,
    max_tokens_per_day: int = 1_000_000,
    model_substrings: list[str] | None = None,
    ledger_name: str = "gemini",
    state_dir: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Policy:
    """Factory: DENY new LLM calls once today's (Pacific) request or token budget
    for a model family is spent. Shared across all sessions via a local ledger.

    :param max_requests_per_day: keep below the project's free-tier RPD so the
        NextNode API (if it shares the key) keeps some headroom.
    :param max_tokens_per_day: token ceiling across all sessions.
    :param model_substrings: only calls whose model id contains one of these
        (case-insensitive) are counted, e.g. ["gemini"]. None counts every model.
    :param ledger_name: separate ledgers per pool, e.g. "gemini".
    """
    if max_requests_per_day <= 0 or max_tokens_per_day <= 0:
        raise ValueError("daily budgets must be positive")
    needles = [s.lower() for s in (model_substrings or [])]
    base = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    ledger = _DailyLedger(base / f"omnigent_budget_{ledger_name}.json",
                          clock or (lambda: datetime.now(timezone.utc)))

    def applies(event: Event) -> bool:
        return not needles or any(n in _model(event).lower() for n in needles)

    def evaluate(event: Event) -> Response:
        phase = event.get("type")
        if phase not in ("llm_request", "request") or not applies(event):
            return None
        state = event.get("session_state") or {}

        # Prefer one count per model call (llm_request). Harnesses that only emit
        # the per-turn `request` phase are counted per turn instead.
        if phase == "request" and state.get("_nn_saw_llm_request"):
            return None

        total = _session_tokens(event)
        last = float(state.get("_nn_budget_last_tokens") or 0)
        over, day = ledger.check_and_record(1, total - last, max_requests_per_day,
                                            max_tokens_per_day)
        updates = [{"key": "_nn_budget_last_tokens", "action": "set", "value": total}]
        if phase == "llm_request":
            updates.append({"key": "_nn_saw_llm_request", "action": "set", "value": True})

        if over:
            return {
                "result": "DENY",
                "reason": (
                    f"NextNode free-tier budget '{ledger_name}' is used up for "
                    f"{day['day']} (Pacific): {day['requests']}/{max_requests_per_day} "
                    f"requests, {day['tokens']}/{max_tokens_per_day} tokens. "
                    "It resets at midnight Pacific time."
                ),
                "state_updates": updates,
            }
        return {"result": "ALLOW", "state_updates": updates}

    return evaluate


def dispatch_budget(
    limit: int = 6,
    dispatch_tools: list[str] | None = None,
) -> Policy:
    """Factory: cap sub-agent dispatches per session (not per turn).

    Omnigent's builtin spawn_bounds caps fan-out per TURN; this caps the whole
    session, which is what stops a Reasoner <-> Reviewer revise cycle from
    running forever. Default 6 = parse resume + parse JD + reason + review +
    one revision + one re-review.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    tools = frozenset(dispatch_tools or ["sys_session_send", "sys_session_create"])

    def evaluate(event: Event) -> Response:
        if event.get("type") != "tool_call":
            return None
        data = event.get("data") or {}
        if not isinstance(data, dict) or data.get("name") not in tools:
            return None
        count = int((event.get("session_state") or {}).get("_nn_dispatch_count") or 0)
        if count >= limit:
            return {
                "result": "DENY",
                "reason": (f"Dispatch budget reached ({limit} sub-agent runs this session). "
                           "Return the best result so far with the reviewer's open issues."),
            }
        return {"result": "ALLOW",
                "state_updates": [{"key": "_nn_dispatch_count", "action": "set",
                                   "value": count + 1}]}

    return evaluate


def deny_tools(blocked: list[str]) -> Policy:
    """Factory: DENY specific tool names outright — e.g. keep the Reviewer
    read-only even if a write tool is ever added to its MCP allow-list."""
    blocked_set = frozenset(blocked)

    def evaluate(event: Event) -> Response:
        if event.get("type") != "tool_call":
            return None
        data = event.get("data") or {}
        name = data.get("name") if isinstance(data, dict) else None
        # MCP tools may be namespaced, e.g. "nextnode__upsert_user_skills"
        if name and any(name == b or name.endswith(f"__{b}") or name.endswith(f".{b}")
                        for b in blocked_set):
            return {"result": "DENY", "reason": f"Tool '{name}' is not allowed for this agent."}
        return _ALLOW

    return evaluate


# Discoverable via Omnigent's GET /v1/policy-registry
POLICY_REGISTRY = [
    {
        "handler": "nextnode_policies.daily_llm_budget",
        "kind": "factory",
        "description": "Per-day (Pacific) request/token budget shared across sessions",
        "params_schema": {
            "type": "object",
            "properties": {
                "max_requests_per_day": {"type": "integer", "default": 150},
                "max_tokens_per_day": {"type": "integer", "default": 1000000},
                "model_substrings": {"type": "array", "items": {"type": "string"}},
                "ledger_name": {"type": "string", "default": "gemini"},
            },
        },
    },
    {
        "handler": "nextnode_policies.dispatch_budget",
        "kind": "factory",
        "description": "Caps sub-agent dispatches per session to stop revise loops",
        "params_schema": {"type": "object",
                          "properties": {"limit": {"type": "integer", "default": 6}}},
    },
    {
        "handler": "nextnode_policies.deny_tools",
        "kind": "factory",
        "description": "Denies the listed tool names",
        "params_schema": {"type": "object",
                          "properties": {"blocked": {"type": "array",
                                                     "items": {"type": "string"}}},
                          "required": ["blocked"]},
    },
]
