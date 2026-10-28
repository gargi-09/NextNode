"""The agent harness: retry/backoff, per-tool breakers, faults, checkpoints and resume, rollback,
the approval queue, evaluators, tracing, record/replay, and the eval bookkeeping.

Everything runs against fakes (a fake OpenAI-compatible server and fake MCP calls): no model,
Ollama, MCP server or Neo4j is needed.
"""
import asyncio
import json

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import harness  # noqa: F401  (puts scripts/ on sys.path)
from eval import run_eval
from harness.approvals import ApprovalQueue
from harness.checkpoints import Checkpoints
from harness.evaluators import deterministic_verdict, run_evaluators
from harness.faults import Faults
from harness.loop import Config, Harness, Task
from harness.replay import Fixture, ModelClient, ReplayMiss
from harness.tools import Breaker, ToolRunner, coded, error_code
from harness.tracing import Tracer

GAP = {"fit_score": 0.5, "scoring_version": "1", "roadmap": [
    {"skill": "Python", "priority": "high", "reason": "below level"},
    {"skill": "PyTorch", "priority": "medium", "reason": "quick to learn"}]}
PREREQS = {"PyTorch": [{"skill": "Python", "depth": 1}], "Python": []}


def module(order, skill, priority, why):
    return {"order": order, "skill": skill, "priority": priority, "why": why, "estimated_hours": 20,
            "builds_on": [], "activities": [{"type": "project", "description": "build something"}]}


GOOD = {"user_id": "u", "job_id": "j", "fit_score": 0.5, "scoring_version": "1", "unmapped_skills": [],
        "modules": [module(1, "Python", "high", "below level"), module(2, "PyTorch", "medium", "quick to learn")]}
RESUME = {"kind": "resume", "skills": [{"name": "Python", "proficiency": 0.8}]}
JOB = {"kind": "job", "title": "ML Engineer", "core_skills": [{"name": "PyTorch", "weight": 0.6}]}


def run(coro):
    return asyncio.run(coro)


async def no_sleep(_):
    return None


# ─────────────────────────── fakes ───────────────────────────

class FakeGraph:
    """Fake MCP calls. `fail` maps tool -> list of error codes returned on successive calls."""

    def __init__(self, fail=None, unmapped=0):
        self.calls, self.fail, self.unmapped = [], {k: list(v) for k, v in (fail or {}).items()}, unmapped

    async def __call__(self, name, arguments):
        self.calls.append(name)
        if self.fail.get(name):
            return False, coded(self.fail[name].pop(0), "fake failure")
        if name == "calculate_skill_gap":
            return True, json.dumps(GAP)
        if name == "get_skill_prerequisites":
            return True, json.dumps({"skill": arguments["skill"], "prerequisites": PREREQS.get(arguments["skill"], [])})
        if name == "get_user_profile":
            return True, json.dumps({"skills": [{"name": "Python"}], "unmapped_skills": ["x"] * self.unmapped})
        if name == "get_job_posting":
            return True, json.dumps({"unmapped_skills": []})
        return True, json.dumps({"ok": True, "unmapped_skills": []})


def tool_call(name, args):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def answer(obj):
    return {"role": "assistant", "content": json.dumps(obj) if not isinstance(obj, str) else obj}


class FakeModels:
    def __init__(self, replies: list):
        self.replies, self.requests = iter(replies), 0

    def client(self):
        def handler(request):
            self.requests += 1
            return httpx.Response(200, json={"choices": [{"message": next(self.replies)}],
                                             "usage": {"prompt_tokens": 100, "completion_tokens": 10}})
        return httpx.Client(base_url="http://fake", transport=httpx.MockTransport(handler))


MCP_TOOLS = [type("T", (), {"name": n, "description": "", "inputSchema": {"type": "object"}})()
             for n in ("calculate_skill_gap", "get_skill_prerequisites", "get_user_profile",
                       "get_job_posting", "upsert_user_skills", "upsert_job_posting", "resolve_skills")]


def make(tmp_path, replies, graph=None, **config):
    models = FakeModels(replies)
    client = models.client()
    h = Harness({"vllm-gpu": client, "ollama-local": client}, graph or FakeGraph(), MCP_TOOLS,
                Config(reviewer=False, **config), runs_dir=tmp_path / "runs",
                approvals=ApprovalQueue(tmp_path / "approvals.jsonl"), sleep=no_sleep,
                tracer_factory=lambda d: Tracer(d, exporter=InMemorySpanExporter()))
    return h, models


TASK = Task(id="t", user_id="u", job_id="j", resume=RESUME, job=JOB)


# ─────────────────────────── tools: retry, backoff, breaker ───────────────────────────

def test_transient_errors_are_retried_with_exponential_backoff():
    graph, sleeps = FakeGraph(fail={"calculate_skill_gap": ["GRAPH_UNAVAILABLE", "TIMEOUT"]}), []

    async def sleep(s):
        sleeps.append(s)

    tools = ToolRunner(graph, base_delay_s=0.5, sleep=sleep, rng=lambda: 0.0)
    ok, _ = run(tools.call("calculate_skill_gap", {}))
    assert ok and sleeps == [0.5, 1.0]
    assert (tools.stats.retries, tools.stats.recovered, tools.stats.attempts) == (2, 1, 3)


def test_permanent_errors_are_not_retried():
    graph = FakeGraph(fail={"get_job_posting": ["NOT_FOUND"]})
    tools = ToolRunner(graph, sleep=no_sleep)
    ok, text = run(tools.call("get_job_posting", {}))
    assert not ok and error_code(text) == "NOT_FOUND" and graph.calls == ["get_job_posting"]


def test_a_slow_tool_times_out_and_is_retried():
    calls = []

    async def slow_then_fast(name, args):
        calls.append(name)
        if len(calls) == 1:
            await asyncio.sleep(1)
        return True, "{}"

    tools = ToolRunner(slow_then_fast, timeout_s=0.05, sleep=no_sleep)
    ok, _ = run(tools.call("calculate_skill_gap", {}))
    assert ok and len(calls) == 2 and tools.stats.by_code == {"TIMEOUT": 1}


def test_breaker_opens_per_tool_and_recovers_after_cooldown():
    now = [0.0]
    graph = FakeGraph(fail={"calculate_skill_gap": ["GRAPH_UNAVAILABLE"] * 9})
    tools = ToolRunner(graph, max_attempts=3, breaker_threshold=3, breaker_cooldown_s=30,
                       sleep=no_sleep, clock=lambda: now[0])
    assert not run(tools.call("calculate_skill_gap", {}))[0]       # 3 failed attempts open it
    assert tools.breaker("calculate_skill_gap").state == "open" and tools.stats.breaker_opens == 1
    assert len(graph.calls) == 3 and tools.stats.retries == 2
    before = len(graph.calls)
    ok, text = run(tools.call("calculate_skill_gap", {}))
    assert error_code(text) == "CIRCUIT_OPEN" and len(graph.calls) == before   # not called while open
    assert run(tools.call("get_user_profile", {"user_id": "u"}))[0]               # other tools unaffected
    now[0] = 31.0                                                                 # half-open: one probe
    graph.fail["calculate_skill_gap"] = []
    assert run(tools.call("calculate_skill_gap", {}))[0]
    assert tools.breaker("calculate_skill_gap").state == "closed"


def test_the_breaker_stops_retries_and_a_permanent_error_resets_it():
    graph = FakeGraph(fail={"calculate_skill_gap": ["GRAPH_UNAVAILABLE", "GRAPH_UNAVAILABLE", "NOT_FOUND",
                                                    "GRAPH_UNAVAILABLE", "GRAPH_UNAVAILABLE"]})
    tools = ToolRunner(graph, max_attempts=5, breaker_threshold=2, sleep=no_sleep)
    run(tools.call("calculate_skill_gap", {}))
    assert len(graph.calls) == 2 and tools.breaker("calculate_skill_gap").state == "open"   # 2 of 5 attempts
    tools = ToolRunner(graph, max_attempts=3, breaker_threshold=2, sleep=no_sleep)
    ok, text = run(tools.call("calculate_skill_gap", {}))                   # NOT_FOUND: the tool answered
    assert error_code(text) == "NOT_FOUND" and tools.breaker("calculate_skill_gap").state == "closed"


def test_breaker_reopens_when_the_probe_fails():
    now = [0.0]
    b = Breaker(threshold=1, cooldown_s=10, clock=lambda: now[0])
    b.failure()
    now[0] = 11
    assert b.state == "half_open"
    b.failure()
    assert b.state == "open" and b.opens == 2


def test_faults_are_deterministic():
    f = Faults({"tool": {"a": {"code": "TIMEOUT", "times": 2}, "b": {"code": "INTERNAL", "times": -1}},
                "model": {"reasoner": {"times": 1}}})
    assert [f.tool_fault("a") for _ in range(3)] == ["TIMEOUT", "TIMEOUT", None]
    assert all(f.tool_fault("b") == "INTERNAL" for _ in range(5))
    assert f.model_fault("reasoner") == "malformed" and f.model_fault("reasoner") is None
    worse = Faults({"revision": "reverse_modules_and_fit"}).corrupt("revision", GOOD)
    assert [m["skill"] for m in worse["modules"]] == ["PyTorch", "Python"] and worse["fit_score"] == 0.75


# ─────────────────────────── checkpoints, approvals, evaluators, tracing ───────────────────────────

def test_checkpoints_save_and_resume(tmp_path):
    cp = Checkpoints(tmp_path / "run")
    assert cp.latest() is None
    cp.save("parsed", {"parsed": {"resume": RESUME}})
    cp.save("drafted", {"parsed": {}, "draft": GOOD})
    stage, state = cp.latest()
    assert stage == "drafted" and state["draft"] == GOOD and cp.done("parsed") and not cp.done("verified")
    with pytest.raises(ValueError):
        cp.save("unknown", {})


def test_approval_queue_is_append_only_and_persists(tmp_path):
    q = ApprovalQueue(tmp_path / "a.jsonl", clock=lambda: 1.0)
    q.submit("r1", ["verdict REVISE"], {"user_id": "u"})
    q.submit("r2", ["rollback"], {"user_id": "v"})
    assert [i["run_id"] for i in ApprovalQueue(tmp_path / "a.jsonl").pending()] == ["r1", "r2"]
    assert q.decide("r1", approve=True, note="ok")["status"] == "approved"
    with pytest.raises(ValueError):
        q.decide("r1", approve=False)
    with pytest.raises(KeyError):
        q.decide("nope", approve=True)
    assert [i["run_id"] for i in q.pending()] == ["r2"]
    assert len((tmp_path / "a.jsonl").read_text().splitlines()) == 3


def test_evaluators_recompute_the_verdict_from_graph_facts():
    payload = {"curriculum": GOOD, "gap": GAP, "prereqs": {"PyTorch": ["Python"], "Python": []}, "owned": ["Python"]}
    results = run_evaluators(payload)
    assert len(results) == 7 and deterministic_verdict(results) == "PASS"
    wrong = {**GOOD, "modules": list(reversed(GOOD["modules"]))}
    results = run_evaluators({**payload, "curriculum": wrong})
    failed = {r.name for r in results if r.label == "fail"}
    assert failed == {"prerequisite_order"} and deterministic_verdict(results) == "REVISE"
    assert all(r.annotator_kind == "CODE" for r in results)


def test_spans_carry_only_allowed_attributes():
    exporter = InMemorySpanExporter()
    tracer = Tracer(exporter=exporter)
    with tracer.span("tool_call", {"tool.name": "x", "arguments": "{\"resume\": \"secret\"}",
                                   "api_key": "k"}) as span:
        span.set(outcome="ok", body="resume text", attempts=1)
    tracer.shutdown()
    (s,) = exporter.get_finished_spans()
    assert dict(s.attributes) == {"tool.name": "x", "outcome": "ok", "attempts": 1}


def test_record_then_replay(tmp_path):
    fx = Fixture(tmp_path / "f.json")
    models = FakeModels([answer({"a": 1})])
    rec = ModelClient("parser", mode="record", http=models.client(), fixture=fx, task="t1")
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    assert rec.post("/v1/chat/completions", json=body).json()["choices"][0]["message"]["content"] == '{"a": 1}'
    fx.save()
    rep = ModelClient("parser", mode="replay", fixture=Fixture(tmp_path / "f.json"), task="t1")
    assert rep.post("/v1/chat/completions", json=body).json()["usage"]["prompt_tokens"] == 100
    assert rep.drift == 0
    with pytest.raises(ReplayMiss):
        rep.post("/v1/chat/completions", json=body)
    other = ModelClient("parser", mode="replay", fixture=Fixture(tmp_path / "f.json"), task="t1")
    other.post("/v1/chat/completions", json={**body, "messages": []})
    assert other.drift == 1


# ─────────────────────────── the loop, end to end with fakes ───────────────────────────

REASONER_OK = [tool_call("calculate_skill_gap", {"user_id": "u", "job_id": "j"}), answer(GOOD)]


def test_a_good_draft_ships(tmp_path):
    h, _ = make(tmp_path, REASONER_OK)
    result = run(h.run(TASK, run_id="r"))
    assert result["status"] == "shipped" and result["verdict"] == "PASS" and result["revisions"] == 0
    assert result["tokens"] == {"prompt": 200, "completion": 20, "total": 220, "model_calls": 2, "source": "live"}
    assert sorted(p.name for p in (tmp_path / "runs" / "r").glob("checkpoint-*")) == [
        "checkpoint-01-parsed.json", "checkpoint-02-ingested.json", "checkpoint-03-drafted.json",
        "checkpoint-04-verified.json",
        "checkpoint-06-final.json"]
    assert not h.approvals.pending()


def test_the_harness_writes_the_user_and_job_and_the_reasoner_only_reads(tmp_path):
    graph = FakeGraph()
    h, models = make(tmp_path, REASONER_OK, graph)
    seen = []
    handler = models.client()._transport.handler

    def spy(request):
        seen.append({t["function"]["name"] for t in json.loads(request.content).get("tools") or []})
        return handler(request)

    client = httpx.Client(base_url="http://fake", transport=httpx.MockTransport(spy))
    h.clients = {"vllm-gpu": client, "ollama-local": client}
    result = run(h.run(TASK))
    assert result["status"] == "shipped"
    assert graph.calls[:2] == ["upsert_user_skills", "upsert_job_posting"]        # in code, before the draft
    assert seen and all(not names & {"upsert_user_skills", "upsert_job_posting"} for names in seen)


def test_a_malformed_first_reasoner_turn_no_longer_loses_the_upsert(tmp_path):
    # eval-16: the injected malformed turn used to replace the reasoner's upsert call
    h, _ = make(tmp_path, REASONER_OK + REASONER_OK)
    result = run(h.run(Task(**{**vars(TASK), "faults": {"model": {"reasoner": {"times": 1}}}})))
    assert result["status"] == "shipped"


def test_a_failed_upsert_fails_the_run_as_retrieval_before_any_model_call(tmp_path):
    graph = FakeGraph(fail={"upsert_user_skills": ["INVALID_INPUT"]})
    h, models = make(tmp_path, REASONER_OK, graph)
    result = run(h.run(TASK))
    assert result["status"] == "failed" and result["failure_category"] == "retrieval" and models.requests == 0


def test_a_transient_tool_failure_is_recovered(tmp_path):
    graph = FakeGraph(fail={"calculate_skill_gap": ["TIMEOUT"]})
    h, _ = make(tmp_path, REASONER_OK, graph)
    result = run(h.run(TASK))
    assert result["status"] == "shipped" and result["tools"]["recovered"] == 1


def test_a_dead_tool_opens_the_breaker_and_fails_as_retrieval(tmp_path):
    graph = FakeGraph(fail={"calculate_skill_gap": ["GRAPH_UNAVAILABLE"] * 50})
    h, _ = make(tmp_path, [tool_call("calculate_skill_gap", {"user_id": "u", "job_id": "j"}),
                           tool_call("calculate_skill_gap", {"user_id": "u", "job_id": "j"}),
                           tool_call("calculate_skill_gap", {"user_id": "u", "job_id": "j"}),
                           answer(GOOD)], graph)
    result = run(h.run(TASK))
    assert result["status"] == "failed" and result["failure_category"] == "retrieval"
    assert result["tools"]["breaker_opens"] >= 1 and "calculate_skill_gap" in result["tools"]["breakers_open"]


def test_malformed_model_output_twice_fails_as_model(tmp_path):
    h, _ = make(tmp_path, [answer(GOOD), answer(GOOD)])
    result = run(h.run(Task(**{**vars(TASK), "faults": {"model": {"reasoner": {"times": 2}}}})))
    assert result["status"] == "failed" and result["failure_category"] == "model"
    assert not (tmp_path / "runs" / result["run_id"] / "checkpoint-06-final.json").exists()


def test_a_worse_revision_is_rolled_back_and_queued(tmp_path):
    h, _ = make(tmp_path, [*REASONER_OK, answer(GOOD)])
    task = Task(**{**vars(TASK), "faults": {"draft": "reverse_modules", "revision": "reverse_modules_and_fit"}})
    result = run(h.run(task, run_id="rb"))
    assert result["rollback"] and result["revisions"] == 1 and result["status"] == "needs_approval"
    assert [m["skill"] for m in result["curriculum"]["modules"]] == ["PyTorch", "Python"]   # the earlier draft
    assert [i["run_id"] for i in h.approvals.pending()] == ["rb"]


def test_a_better_revision_ships(tmp_path):
    h, _ = make(tmp_path, [*REASONER_OK, answer(GOOD)])
    result = run(h.run(Task(**{**vars(TASK), "faults": {"draft": "reverse_modules"}})))
    assert result["status"] == "shipped" and result["revisions"] == 1 and not result["rollback"]


def test_many_unmapped_skills_go_to_the_approval_queue(tmp_path):
    h, _ = make(tmp_path, REASONER_OK, FakeGraph(unmapped=3))
    result = run(h.run(TASK))
    assert result["status"] == "needs_approval" and "unmapped" in result["approval_reasons"][0]


def test_resume_skips_completed_stages_and_finished_runs_are_not_requeued(tmp_path):
    h, models = make(tmp_path, [])            # the model must not be called at all
    cp = Checkpoints(tmp_path / "runs" / "res")
    cp.save("drafted", {"parsed": {"resume": RESUME, "job": JOB}, "draft": GOOD, "curriculum": GOOD})
    result = run(h.run(TASK, run_id="res"))
    assert result["status"] == "shipped" and models.requests == 0
    again = run(h.run(TASK, run_id="res"))
    assert again["run_id"] == "res" and not h.approvals.pending()


def test_replay_mode_needs_no_model_server(tmp_path):
    fx = Fixture(tmp_path / "f.json")
    h, _ = make(tmp_path, REASONER_OK, mode="record")
    h.fixture = fx
    assert run(h.run(TASK))["status"] == "shipped"
    fx.save()
    replay = Harness({}, FakeGraph(), MCP_TOOLS, Config(mode="replay", reviewer=False),
                     fixture=Fixture(tmp_path / "f.json"), runs_dir=tmp_path / "runs2",
                     approvals=ApprovalQueue(tmp_path / "a2.jsonl"), sleep=no_sleep,
                     tracer_factory=lambda d: Tracer(d, exporter=InMemorySpanExporter()))
    result = run(replay.run(TASK))
    assert result["status"] == "shipped" and result["tokens"]["source"] == "recorded" and result["drift"] == 0


def test_the_run_writes_spans_for_stages_and_tool_calls(tmp_path):
    exporter = InMemorySpanExporter()
    h, _ = make(tmp_path, REASONER_OK)
    h.tracer_factory = lambda d: Tracer(d, exporter=exporter)
    run(h.run(TASK))
    names = [s.name for s in exporter.get_finished_spans()]
    assert {"run", "stage", "model_turn", "tool_call"} <= set(names)
    tool_spans = [s for s in exporter.get_finished_spans() if s.name == "tool_call"]
    assert all({"tool.name", "attempts", "outcome"} <= set(s.attributes) for s in tool_spans)


# ─────────────────────────── eval bookkeeping ───────────────────────────

def test_eval_tasks_are_well_formed():
    tasks = run_eval.load_tasks()
    assert len(tasks) == 20 and len({t["id"] for t in tasks}) == 20
    assert sum(t["category"] == "fault" for t in tasks) == 8
    for t in tasks:
        assert t["user_id"].startswith("eval-") and t["job_id"].startswith("eval-"), t["id"]
        assert set(t["expect"]["status"]) <= {"shipped", "needs_approval", "failed"}, t["id"]
        Task.from_dict(t)


def test_expectations_percentiles_and_summary():
    base = {"status": "failed", "failure_category": "retrieval", "rollback": False,
            "tools": {"recovered": 0, "breaker_opens": 1, "calls": 3, "retries": 2}}
    assert run_eval.check_expectation(base, {"status": ["failed"], "failure_category": "retrieval",
                                             "min_breaker_opens": 1}) == []
    assert run_eval.check_expectation(base, {"status": ["shipped"]})
    assert run_eval.cause({**base, "status": "needs_approval"}, passed=False) == "model"
    assert run_eval.percentile([1, 2, 3, 4, 100], 50) == 3 and run_eval.percentile([1, 2, 3, 4, 100], 95) == 100
    rows = [{"passed": True, "cause": None, "status": "shipped", "latency_s": 2.0,
             "tokens": {"total": 10}, "tools": base["tools"]},
            {"passed": False, "cause": "model", "status": "needs_approval", "latency_s": 4.0,
             "tokens": {"total": 30}, "tools": base["tools"]}]
    s = run_eval.summarize(rows, "replay")
    assert s["success_rate"] == 0.5 and s["failure_causes"] == {"model": 1}
    assert s["tokens_per_task"] == {"mean": 20.0, "source": "recorded"}
    assert "| eval" not in run_eval.markdown(s, [])
