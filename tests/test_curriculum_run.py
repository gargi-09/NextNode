"""The scripted curriculum runner: fixed step order, tool allow-lists, one revision max.

Runs the whole sequence against a fake OpenAI-compatible server and fake MCP tool calls,
so no model, Ollama or Neo4j is needed.
"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("curriculum_run", ROOT / "scripts" / "curriculum_run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

TOOLS = [SimpleNamespace(name=n, description="", inputSchema={"type": "object"})
         for n in ("resolve_skills", "get_user_profile", "get_job_posting", "calculate_skill_gap",
                   "get_skill_prerequisites", "upsert_user_skills", "upsert_job_posting")]
GAP = {"fit_score": 0.5, "scoring_version": "1", "roadmap": [
    {"skill": "Python", "priority": "high", "reason": "below level"},
    {"skill": "PyTorch", "priority": "medium", "reason": "quick to learn"}]}
PREREQS = {"PyTorch": [{"skill": "Python", "depth": 1}], "Python": []}


def module(order, skill, priority, why):
    return {"order": order, "skill": skill, "priority": priority, "why": why, "estimated_hours": 20,
            "activities": [{"type": "project", "description": "build something"}]}


CURRICULUM = {"user_id": "u", "job_id": "j", "fit_score": 0.5, "scoring_version": "1",
              "modules": [module(1, "Python", "high", "below level"),
                          module(2, "PyTorch", "medium", "quick to learn")],
              "unmapped_skills": ["Airflow"]}
WRONG_ORDER = {**CURRICULUM, "modules": list(reversed(CURRICULUM["modules"]))}


def tool_call(name, args):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def answer(obj):
    return {"role": "assistant", "content": json.dumps(obj)}


class FakeModels:
    """Replies per model, in order; records the tools each request offered."""

    def __init__(self, script: dict[str, list]):
        self.script = {k: iter(v) for k, v in script.items()}
        self.offered: list[tuple[str, list[str]]] = []

    def transport(self):
        def handler(request: httpx.Request):
            body = json.loads(request.content)
            self.offered.append((body["model"], [t["function"]["name"] for t in body.get("tools", [])]))
            return httpx.Response(200, json={"choices": [{"message": next(self.script[body["model"]])}]})
        return httpx.MockTransport(handler)


def run_flow(models: FakeModels):
    calls = []

    async def call(name, arguments):
        calls.append(name)
        if name == "calculate_skill_gap":
            return True, json.dumps(GAP)
        if name == "get_skill_prerequisites":
            return True, json.dumps({"skill": arguments["skill"], "prerequisites": PREREQS[arguments["skill"]]})
        if name == "get_user_profile":
            return True, json.dumps({"id": "u", "skills": [{"name": "NumPy"}, {"name": "Python"}]})
        return True, json.dumps({"ok": True})

    client = httpx.Client(base_url="http://fake", transport=models.transport())
    clients = {"vllm-gpu": client, "ollama-local": client}
    result = asyncio.run(run.run_workflow("u", "j", "resume text", "job text", clients, call, TOOLS))
    return result, calls


PARSES = [answer({"kind": "resume", "skills": [{"name": "Python", "proficiency": 0.8}]}),
          answer({"kind": "job", "core_skills": [{"name": "PyTorch", "weight": 0.6}]})]


def test_fixed_order_parse_reason_review_pass():
    models = FakeModels({
        "qwen3-4b": [*PARSES, tool_call("calculate_skill_gap", {"user_id": "u", "job_id": "j"}), answer(CURRICULUM)],
        "llama3.1:8b": [tool_call("calculate_skill_gap", {"user_id": "u", "job_id": "j"}),
                        answer({"verdict": "PASS", "blocking": []})],
    })
    result, calls = run_flow(models)
    assert result["review"]["verdict"] == "PASS" and result["revisions"] == 0
    assert result["curriculum"] == CURRICULUM and result["unmapped_skills"] == ["Airflow"]
    # reasoner, reviewer, then the code checks: gap + one prerequisite lookup per module
    assert calls == ["calculate_skill_gap"] * 3 + ["get_skill_prerequisites"] * 2 + ["get_user_profile"]
    assert [m for m, _ in models.offered] == ["qwen3-4b"] * 4 + ["llama3.1:8b"] * 2
    assert result["review"]["blocking"] == []


def test_reviewer_never_sees_a_write_tool_and_parser_gets_none():
    models = FakeModels({"qwen3-4b": [*PARSES, answer(CURRICULUM)],
                         "llama3.1:8b": [answer({"verdict": "PASS", "blocking": []})]})
    run_flow(models)
    offered = dict()
    for model, tools in models.offered:
        offered.setdefault(model, []).append(set(tools))
    assert offered["qwen3-4b"][0] == set() and offered["qwen3-4b"][1] == set()       # parser: no tools
    assert run.WRITE_TOOLS <= offered["qwen3-4b"][2]                                   # reasoner writes
    assert not (run.WRITE_TOOLS & offered["llama3.1:8b"][0])                           # reviewer: read-only


def test_a_disallowed_tool_call_is_refused_not_executed():
    models = FakeModels({
        "qwen3-4b": [*PARSES, answer(CURRICULUM)],
        "llama3.1:8b": [tool_call("upsert_user_skills", {"user_id": "u", "skills": []}),
                        answer({"verdict": "PASS", "blocking": []})],
    })
    _, calls = run_flow(models)
    assert "upsert_user_skills" not in calls


def test_at_most_one_revision():
    revise = answer({"verdict": "REVISE", "blocking": [{"check": 1, "issue": "fit_score differs"}]})
    models = FakeModels({"qwen3-4b": [*PARSES, answer(WRONG_ORDER), answer(WRONG_ORDER)],
                         "llama3.1:8b": [revise, revise]})
    result, _ = run_flow(models)
    assert result["revisions"] == 1 and result["review"]["verdict"] == "REVISE"


def test_the_graph_checks_decide_not_the_reviewer_model():
    """Llama 3.1 claimed a failure for every check on a correct curriculum (first local run)."""
    claims = [{"check": n, "issue": "does not match"} for n in range(1, 8)]
    models = FakeModels({"qwen3-4b": [*PARSES, answer(CURRICULUM)],
                         "llama3.1:8b": [answer({"verdict": "REVISE", "blocking": claims})]})
    result, _ = run_flow(models)
    assert result["review"]["verdict"] == "PASS" and result["revisions"] == 0
    assert len(result["review"]["reviewer_claims"]) == 7


def test_invalid_json_is_retried_once():
    models = FakeModels({"qwen3-4b": [{"role": "assistant", "content": "Sure! Here it is"}, *PARSES,
                                      answer(CURRICULUM)],
                         "llama3.1:8b": [answer({"verdict": "PASS", "blocking": []})]})
    result, _ = run_flow(models)
    assert result["parsed"]["resume"]["kind"] == "resume"


def test_extract_json():
    assert run.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert run.extract_json('Here: {"a": {"b": 2}} done') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        run.extract_json("no json here")


def test_agents_and_providers_come_from_the_omnigent_bundle():
    urls = run.provider_urls()
    for name in ("parser", "reasoner", "reviewer"):
        agent = run.load_agent(name)
        assert agent["provider"] in urls and agent["prompt"].strip()
    assert run.load_agent("reviewer")["model"] != run.load_agent("reasoner")["model"]


def test_a_tool_call_written_as_text_is_executed():
    """Llama 3.1 on Ollama sometimes writes the call as JSON text instead of tool_calls."""
    text_call = {"role": "assistant", "content": json.dumps(
        {"name": "calculate_skill_gap", "parameters": {"user_id": "u", "job_id": "j"}})}
    models = FakeModels({"qwen3-4b": [*PARSES, answer(CURRICULUM)],
                         "llama3.1:8b": [text_call, answer({"verdict": "PASS", "blocking": []})]})
    result, calls = run_flow(models)
    assert calls[0] == "calculate_skill_gap" and result["review"]["verdict"] == "PASS"


def test_a_review_without_a_verdict_is_retried():
    models = FakeModels({"qwen3-4b": [*PARSES, answer(CURRICULUM)],
                         "llama3.1:8b": [answer({"looks": "fine"}), answer({"verdict": "PASS", "blocking": []})]})
    result, _ = run_flow(models)
    assert result["review"]["verdict"] == "PASS"


def test_code_checks_catch_a_module_before_its_prerequisite():
    """The reviewer said PASS, but PyTorch comes before Python: the code check forces a revision."""
    models = FakeModels({"qwen3-4b": [*PARSES, answer(WRONG_ORDER), answer(CURRICULUM)],
                         "llama3.1:8b": [answer({"verdict": "PASS", "blocking": []}),
                                         answer({"verdict": "PASS", "blocking": []})]})
    result, _ = run_flow(models)
    assert result["revisions"] == 1 and result["review"]["verdict"] == "PASS"
    assert result["curriculum"]["modules"][0]["skill"] == "Python"


def test_code_checks():
    prereqs = {"PyTorch": ["Python"], "Python": []}
    assert run.code_checks(CURRICULUM, GAP, prereqs) == []
    issues = run.code_checks({**WRONG_ORDER, "fit_score": 0.8}, GAP, prereqs)
    assert {i["check"] for i in issues} == {1, 3}
    pytorch_first = {**CURRICULUM, "modules": [{**CURRICULUM["modules"][1], "builds_on": ["PyTorch"]}]}
    not_owned = run.code_checks(pytorch_first, GAP, prereqs, owned={"Python"})
    assert (4, "PyTorch") in [(i["check"], i["module"]) for i in not_owned]
    missing = run.code_checks({**CURRICULUM, "modules": CURRICULUM["modules"][1:]}, GAP, prereqs)
    assert [(i["check"], i["module"]) for i in missing] == [(2, "Python")]


def test_builds_on_may_name_a_skill_taught_by_an_earlier_module():
    """Third local run: HuggingFace built on PyTorch, taught one module earlier. That is fine."""
    later = {**CURRICULUM, "modules": [CURRICULUM["modules"][0],
                                       {**CURRICULUM["modules"][1], "builds_on": ["Python"]}]}
    issues = run.code_checks(later, GAP, {"PyTorch": ["Python"], "Python": []}, owned=set())
    assert not [i for i in issues if i["check"] == 4]
