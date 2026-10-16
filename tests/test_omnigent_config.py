"""Omnigent layer: custom policy behavior + consistency of the YAML configs.

Omnigent itself is not installed in this venv; these tests exercise the policy
functions directly with event dicts shaped like Omnigent v0.18.0's PolicyEvent,
and check the configs agree with each other and with the TDD tool contract.
"""
import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
OMNI = ROOT / "omnigent"
BUNDLE = OMNI / "nextnode_curriculum"

_spec = importlib.util.spec_from_file_location(
    "nextnode_policies", OMNI / "policies" / "nextnode_policies.py")
policies = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policies)

PT = policies.QUOTA_TZ
WRITE_TOOLS = {"upsert_user_skills", "upsert_job_posting"}
READ_TOOLS = {"resolve_skills", "get_user_profile", "get_job_posting",
              "calculate_skill_gap", "get_skill_prerequisites"}


class Session:
    """Applies state_updates the way Omnigent does, so multi-call tests are realistic."""
    def __init__(self, model="gemini-2.5-flash"):
        self.state = {}
        self.tokens = 0
        self.model = model

    def event(self, phase="llm_request", tokens_used=0, **extra):
        self.tokens += tokens_used
        return {"type": phase, "context": {"model": self.model,
                                           "usage": {"total_tokens": self.tokens}},
                "session_state": dict(self.state), **extra}

    def apply(self, response):
        for u in (response or {}).get("state_updates", []):
            if u["action"] == "set":
                self.state[u["key"]] = u["value"]
            elif u["action"] == "increment":
                self.state[u["key"]] = self.state.get(u["key"], 0) + u["value"]
        return response


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


# ─────────────────────────── daily_llm_budget ───────────────────────────

def _budget(tmp_path, clock, **kw):
    return policies.daily_llm_budget(state_dir=str(tmp_path), clock=clock,
                                     model_substrings=["gemini"], **kw)


def test_request_budget_is_shared_across_sessions(tmp_path):
    clock = Clock(datetime(2026, 10, 15, 9, tzinfo=PT))
    policy = _budget(tmp_path, clock, max_requests_per_day=3)
    a, b = Session(), Session()

    for s in (a, b, a):
        assert s.apply(policy(s.event()))["result"] == "ALLOW"
    denied = b.apply(policy(b.event()))
    assert denied["result"] == "DENY"
    assert "resets at midnight Pacific" in denied["reason"]


def test_budget_resets_at_pacific_midnight(tmp_path):
    clock = Clock(datetime(2026, 10, 15, 23, 59, tzinfo=PT))
    policy = _budget(tmp_path, clock, max_requests_per_day=1)
    s = Session()
    assert s.apply(policy(s.event()))["result"] == "ALLOW"
    assert s.apply(policy(s.event()))["result"] == "DENY"
    clock.dt = datetime(2026, 10, 16, 0, 1, tzinfo=PT)
    assert s.apply(policy(s.event()))["result"] == "ALLOW"


def test_token_budget_uses_per_session_deltas(tmp_path):
    clock = Clock(datetime(2026, 10, 15, 9, tzinfo=PT))
    policy = _budget(tmp_path, clock, max_requests_per_day=100, max_tokens_per_day=1000)
    s = Session()
    s.apply(policy(s.event(tokens_used=0)))
    s.apply(policy(s.event(tokens_used=600)))   # cumulative 600 → +600
    s.apply(policy(s.event(tokens_used=300)))   # cumulative 900 → +300, not +900
    ledger = json.loads((tmp_path / "omnigent_budget_gemini.json").read_text())
    assert ledger["tokens"] == 900
    assert s.apply(policy(s.event(tokens_used=200)))["result"] == "DENY"


def test_local_models_are_not_counted(tmp_path):
    clock = Clock(datetime(2026, 10, 15, 9, tzinfo=PT))
    policy = _budget(tmp_path, clock, max_requests_per_day=1)
    local = Session(model="qwen3:8b")
    for _ in range(5):
        assert policy(local.event()) is None


def test_per_turn_request_counted_only_without_llm_request(tmp_path):
    clock = Clock(datetime(2026, 10, 15, 9, tzinfo=PT))
    policy = _budget(tmp_path, clock, max_requests_per_day=100)

    turn_only = Session()                              # harness emits only `request`
    turn_only.apply(policy(turn_only.event("request")))
    both = Session()                                   # harness emits both phases
    both.apply(policy(both.event("llm_request")))
    assert policy(both.event("request")) is None       # not double-counted

    ledger = json.loads((tmp_path / "omnigent_budget_gemini.json").read_text())
    assert ledger["requests"] == 2


def test_other_phases_abstain(tmp_path):
    policy = _budget(tmp_path, Clock(datetime(2026, 10, 15, 9, tzinfo=PT)))
    assert policy(Session().event("tool_call")) is None


# ─────────────────────────── dispatch_budget / deny_tools ───────────────────────────

def _dispatch(s, name="sys_session_send"):
    return s.event("tool_call", data={"name": name, "arguments": {}})


def test_dispatch_budget_caps_the_whole_session():
    policy = policies.dispatch_budget(limit=2)
    s = Session()
    assert s.apply(policy(_dispatch(s)))["result"] == "ALLOW"
    assert s.apply(policy(_dispatch(s, "sys_session_create")))["result"] == "ALLOW"
    assert s.apply(policy(_dispatch(s)))["result"] == "DENY"
    assert policy(_dispatch(s, "sys_read_inbox")) is None   # other tools unaffected


def test_deny_tools_handles_namespaced_mcp_names():
    policy = policies.deny_tools(["upsert_user_skills"])
    s = Session()
    for name in ("upsert_user_skills", "nextnode__upsert_user_skills", "nextnode.upsert_user_skills"):
        assert policy(s.event("tool_call", data={"name": name}))["result"] == "DENY"
    assert policy(s.event("tool_call", data={"name": "calculate_skill_gap"}))["result"] == "ALLOW"


def test_factories_reject_invalid_limits():
    with pytest.raises(ValueError):
        policies.dispatch_budget(limit=0)
    with pytest.raises(ValueError):
        policies.daily_llm_budget(max_requests_per_day=0)


# ─────────────────────────── config consistency ───────────────────────────

def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


ORCH = _load(BUNDLE / "config.yaml")
AGENTS = {name: _load(BUNDLE / "agents" / name / "config.yaml")
          for name in ORCH["tools"]["agents"]}
SERVER = _load(OMNI / "server_config.yaml")


def test_every_declared_subagent_has_a_spec():
    assert set(AGENTS) == {"parser", "reasoner", "reviewer"}
    for name, spec in AGENTS.items():
        assert spec["spec_version"] == 1
        assert spec["name"] == name
        assert spec["executor"]["harness"]
        assert spec["prompt"].strip()


def test_cross_vendor_review():
    family = lambda m: re.match(r"[a-z]+", m.lower()).group(0)   # qwen3-4b → qwen
    assert family(AGENTS["reviewer"]["executor"]["model"]) != \
           family(AGENTS["reasoner"]["executor"]["model"])
    assert AGENTS["reviewer"]["executor"]["auth"]["name"] != \
           AGENTS["reasoner"]["executor"]["auth"]["name"]


def test_tool_access_matches_the_contract():
    assert "tools" not in AGENTS["parser"]
    assert "tools" not in ORCH or set(ORCH["tools"]) == {"agents"}   # orchestrator: no NextNode tools

    reasoner_tools = set(AGENTS["reasoner"]["tools"]["nextnode"]["tools"])
    reviewer_tools = set(AGENTS["reviewer"]["tools"]["nextnode"]["tools"])
    assert reasoner_tools <= READ_TOOLS | WRITE_TOOLS
    assert WRITE_TOOLS <= reasoner_tools
    assert reviewer_tools <= READ_TOOLS
    blocked = set(AGENTS["reviewer"]["policies"]["read_only"]["factory_params"]["blocked"])
    assert WRITE_TOOLS <= blocked


def test_mcp_endpoint_is_loopback_with_token_from_env():
    for name in ("reasoner", "reviewer"):
        mcp = AGENTS[name]["tools"]["nextnode"]
        assert mcp["type"] == "mcp"
        assert re.match(r"http://127\.0\.0\.1:\d+/mcp$", mcp["url"])
        assert mcp["headers"]["Authorization"] == "Bearer ${NEXTNODE_MCP_TOKEN}"


def _all_policy_entries():
    yield from SERVER["policies"].values()
    yield from (ORCH.get("policies") or {}).values()
    for spec in AGENTS.values():
        yield from (spec.get("policies") or {}).values()


def test_every_custom_policy_handler_exists_with_valid_params():
    for entry in _all_policy_entries():
        assert entry["type"] == "function"
        assert not ("factory_params" in entry and "function" in entry)
        module, _, fn = entry["handler"].rpartition(".")
        if module == "nextnode_policies":
            factory = getattr(policies, fn)
            factory(**entry.get("factory_params", {}))   # builds without error
        else:
            assert module.startswith("omnigent.policies.builtins."), entry["handler"]
    assert "nextnode_policies" in SERVER["policy_modules"]


def test_server_budget_only_counts_gemini():
    params = SERVER["policies"]["gemini_daily_budget"]["factory_params"]
    assert params["model_substrings"] == ["gemini"]
    assert 0 < params["max_requests_per_day"] < 250


def test_no_secrets_in_configs():
    pattern = re.compile(r"(AIza[0-9A-Za-z_\-]{20,}|sk-[A-Za-z0-9]{20,}|password\s*:)", re.I)
    for path in OMNI.rglob("*.yaml"):
        assert not pattern.search(path.read_text(encoding="utf-8")), path
