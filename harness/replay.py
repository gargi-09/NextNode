"""Model access with record and replay, so the 20-task eval can rerun in minutes at $0.

The agents call `client.post("/v1/chat/completions", json=body)` exactly as on an httpx.Client.
ModelClient wraps that call:

  live     forward to the model server
  record   forward, and append the request hash and the response to a fixture file
  replay   return the recorded response for the same (task, agent, turn); the model server is
           never called. A different request hash is counted as drift (the conversation
           changed since recording), and a missing turn raises ReplayMiss.

Tool calls are never replayed: they always go to the real MCP server and Neo4j. Token usage is
taken from the response's `usage` block and labelled live or recorded.
"""
import hashlib
import json
from pathlib import Path


class ReplayMiss(RuntimeError):
    pass


def request_hash(body: dict) -> str:
    keep = {k: body.get(k) for k in ("model", "messages", "tools")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:16]


class _Response:
    def __init__(self, data: dict):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self) -> dict:
        return self._data


class Fixture:
    """Recorded turns of one eval run: {task_id: {agent: [ {hash, response}, ... ]}}."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def turns(self, task: str, agent: str) -> list:
        return self.data.setdefault(task, {}).setdefault(agent, [])

    def reset(self, task: str):
        self.data[task] = {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")


class Usage:
    def __init__(self):
        self.prompt = self.completion = self.calls = self.drift = 0

    def add(self, usage: dict | None):
        self.calls += 1
        usage = usage or {}
        self.prompt += int(usage.get("prompt_tokens") or 0)
        self.completion += int(usage.get("completion_tokens") or 0)

    def as_dict(self, source: str) -> dict:
        return {"prompt": self.prompt, "completion": self.completion,
                "total": self.prompt + self.completion, "model_calls": self.calls, "source": source}


class ModelClient:
    def __init__(self, agent: str, *, mode: str = "live", http=None, fixture: Fixture | None = None,
                 task: str = "run", usage: Usage | None = None, faults=None, tracer=None):
        if mode not in ("live", "record", "replay"):
            raise ValueError(f"unknown mode {mode!r}")
        if mode != "live" and fixture is None:
            raise ValueError(f"{mode} mode needs a fixture")
        self.agent, self.mode, self.http, self.fixture, self.task = agent, mode, http, fixture, task
        self.usage, self.faults, self.tracer = usage or Usage(), faults, tracer
        self.turn, self.drift = 0, 0

    def post(self, path: str, json: dict):                      # noqa: A002 - httpx.Client signature
        body, digest = json, request_hash(json)
        span = self.tracer.span("model_turn", {"agent": self.agent, "turn": self.turn,
                                                "mode": self.mode}) if self.tracer else None
        with span if span else _null():
            if self.mode == "replay":
                turns = self.fixture.turns(self.task, self.agent)
                if self.turn >= len(turns):
                    raise ReplayMiss(f"no recorded turn {self.turn} for {self.task}/{self.agent}; record again")
                entry = turns[self.turn]
                if entry["hash"] != digest:
                    self.drift += 1
                    self.usage.drift += 1
                data = entry["response"]
            else:
                r = self.http.post(path, json=body)
                r.raise_for_status()
                data = r.json()
                if self.mode == "record":
                    self.fixture.turns(self.task, self.agent).append({"hash": digest, "response": data})
            self.turn += 1
            self.usage.add(data.get("usage"))
            if span is not None:
                u = data.get("usage") or {}
                span.set(prompt_tokens=int(u.get("prompt_tokens") or 0),
                         completion_tokens=int(u.get("completion_tokens") or 0))
            if self.faults and self.faults.model_fault(self.agent):
                data = _malformed(data)
            return _Response(data)


def _malformed(data: dict) -> dict:
    from harness.faults import MALFORMED
    out = dict(data)
    choice = dict((data.get("choices") or [{}])[0])
    choice["message"] = {"role": "assistant", "content": MALFORMED}
    out["choices"] = [choice]
    return out


class _null:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False
