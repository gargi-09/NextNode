"""NextNode MCP server: tool contract, error codes, bearer auth. No Neo4j or LLM needed:
the services are replaced with fakes, exactly like the REST route tests."""
import asyncio
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

from app import mcp_server
from app.core.errors import ConflictError, NotFoundError
from app.db.neo4j import GraphUnavailableError

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "t" * 32
WRITE_TOOLS = {"upsert_user_skills", "upsert_job_posting"}
READ_TOOLS = {"resolve_skills", "get_user_profile", "get_job_posting",
              "calculate_skill_gap", "get_skill_prerequisites"}


def call(_tool, **args):
    """Call a tool the way an MCP client does and return its JSON result."""
    content = asyncio.run(mcp_server.mcp.call_tool(_tool, args))
    return json.loads(content[0].text)


def tool_error(_tool, **args) -> dict:
    """The coded JSON body of a tool error (FastMCP prefixes "Error executing tool X: ")."""
    with pytest.raises(ToolError) as e:
        asyncio.run(mcp_server.mcp.call_tool(_tool, args))
    text = str(e.value)
    return json.loads(text[text.index("{"):])


# ─────────────────────────── contract ───────────────────────────

def test_exposes_exactly_the_tdd_tool_set_with_correct_hints():
    tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert set(tools) == READ_TOOLS | WRITE_TOOLS
    for name, tool in tools.items():
        assert tool.annotations.readOnlyHint is (name in READ_TOOLS), name
        assert tool.annotations.destructiveHint is False, name
    assert not any("delete" in n or "pivot" in n or "extract" in n for n in tools)


def test_agent_allow_lists_only_name_existing_tools():
    bundle = ROOT / "omnigent" / "nextnode_curriculum" / "agents"
    for agent in ("reasoner", "reviewer"):
        spec = yaml.safe_load((bundle / agent / "config.yaml").read_text(encoding="utf-8"))
        assert set(spec["tools"]["nextnode"]["tools"]) <= READ_TOOLS | WRITE_TOOLS, agent


# ─────────────────────────── tools ───────────────────────────

def test_resolve_skills_is_strict_and_needs_no_database():
    out = call("resolve_skills", names=["k8s", "torch", "Basket Weaving", " "])
    assert [r["canonical"] for r in out["resolved"]] == ["Kubernetes", "PyTorch"]
    assert out["unmapped"] == ["Basket Weaving"]


def test_calculate_skill_gap_drops_ui_payloads(monkeypatch):
    class Fake:
        def model_dump(self, exclude):
            full = {"fit_score": 0.5, "roadmap": [], "graph": {}, "roadmap_flow": {},
                    "explanation": None, "explanation_status": "not_requested"}
            return {k: v for k, v in full.items() if k not in exclude}

    seen = {}

    async def analyze(user_id, job_id, explain):
        seen["explain"] = explain
        return Fake()

    monkeypatch.setattr(mcp_server.analysis, "analyze_gap", analyze)
    out = call("calculate_skill_gap", user_id="u", job_id="j")
    assert out == {"fit_score": 0.5, "roadmap": []}
    assert seen["explain"] is False   # never spends the API's Gemini budget


def test_get_skill_prerequisites_resolves_aliases_first(monkeypatch):
    asked = []
    monkeypatch.setattr(mcp_server.catalog, "skill_prerequisites",
                        lambda name: asked.append(name) or [{"skill": "Python", "depth": 1, "strength": 0.8}])
    out = call("get_skill_prerequisites", skill="torch")
    assert asked == ["PyTorch"] and out["skill"] == "PyTorch"
    assert out["prerequisites"][0]["skill"] == "Python"
    assert tool_error("get_skill_prerequisites", skill="Basket Weaving")["code"] == "NOT_FOUND"


def test_upsert_user_skills_creates_then_replaces(monkeypatch):
    calls = []

    def get_user(uid):
        calls.append(("get", uid))
        raise NotFoundError("missing")

    monkeypatch.setattr(mcp_server.users.catalog, "get_user", get_user)
    monkeypatch.setattr(mcp_server.users.writer, "replace_user_skills",
                        lambda uid, rows, unmapped: calls.append(("replace", uid, [r["name"] for r in rows], unmapped)))
    async def create_user(req):
        calls.append(("create", req.id, req.name))

    monkeypatch.setattr(mcp_server.users, "create_user", create_user)

    out = call("upsert_user_skills", user_id="u1", name="Ada",
               skills=[{"name": "k8s", "proficiency": 0.4}, {"name": "Basket Weaving", "proficiency": 0.7}])
    assert out == {"user_id": "u1", "created": True, "skills_written": ["Kubernetes"],
                   "unmapped_skills": ["Basket Weaving"]}
    assert calls[1:] == [("create", "u1", "Ada"), ("replace", "u1", ["Kubernetes"], ["Basket Weaving"])]


def test_upsert_user_skills_validates_proficiency():
    with pytest.raises(ToolError):
        asyncio.run(mcp_server.mcp.call_tool("upsert_user_skills",
                                             {"user_id": "u1", "skills": [{"name": "Python", "proficiency": 1.5}]}))


def test_upsert_job_posting_overwrites_and_reports_unmapped(monkeypatch):
    written = {}

    def write_job(job_id, meta, rows, domains, unmapped, source_hash, create_only):
        written.update(job_id=job_id, rows=[(r["name"], r["importance"]) for r in rows],
                       domains=domains, unmapped=unmapped, create_only=create_only)
        return False

    monkeypatch.setattr(mcp_server.jobs.writer, "write_job", write_job)
    out = call("upsert_job_posting", job_id="j1", title="MLE", domains=["NLP", "astrology"],
               core_skills=[{"name": "python", "weight": 0.7}],
               preferred_skills=[{"name": "Rust", "weight": 0.5}])
    assert written["create_only"] is False
    assert written["rows"] == [("Python", "core")] and written["domains"] == ["nlp"]
    assert out == {"job_id": "j1", "created": False, "skills_written": ["Python"],
                   "unmapped_skills": ["Rust"], "unmapped_domains": ["astrology"]}


@pytest.mark.parametrize("exc, code", [
    (NotFoundError("User 'u' not found"), "NOT_FOUND"),
    (ConflictError("exists"), "CONFLICT"),
    (GraphUnavailableError(retry_after=30), "GRAPH_UNAVAILABLE"),
    (RuntimeError("secret detail"), "INTERNAL"),
])
def test_service_errors_become_coded_tool_errors(monkeypatch, exc, code):
    async def boom(_):
        raise exc

    monkeypatch.setattr(mcp_server.users, "get_user", boom)
    err = tool_error("get_user_profile", user_id="u")
    assert err["code"] == code
    if code == "GRAPH_UNAVAILABLE":
        assert err["retry_after"] == 30 and err["hint"]
    assert "secret detail" not in json.dumps(err)


# ─────────────────────────── HTTP transport + auth ───────────────────────────

INIT = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
           "Host": "127.0.0.1:8765"}


@pytest.fixture(scope="module")
def client():
    # one app per module: the MCP session manager can only be started once per process
    with TestClient(mcp_server.http_app(TOKEN), base_url="http://127.0.0.1:8765") as c:
        yield c


def test_health_needs_no_token(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "tools": 7}


@pytest.mark.parametrize("auth", [None, "Bearer wrong-token-wrong-token", f"Basic {TOKEN}", TOKEN])
def test_mcp_endpoint_rejects_missing_or_wrong_token(client, auth):
    headers = HEADERS | ({"Authorization": auth} if auth else {})
    r = client.post("/mcp", json=INIT, headers=headers)
    assert r.status_code == 401 and r.json() == {"code": "UNAUTHORIZED"}


def test_mcp_endpoint_lists_tools_with_the_token(client):
    r = client.post("/mcp", json=INIT, headers=HEADERS | {"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200, r.text
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert names == READ_TOOLS | WRITE_TOOLS


def test_rejects_foreign_host_header_even_with_token(client):
    r = client.post("/mcp", json=INIT,
                    headers=HEADERS | {"Host": "evil.example", "Authorization": f"Bearer {TOKEN}"})
    assert r.status_code in (403, 421)


def test_refuses_weak_or_missing_token_and_non_loopback_host():
    for bad in ("", "short"):
        with pytest.raises(ValueError):
            mcp_server.http_app(bad)
    with pytest.raises(SystemExit):
        mcp_server.main(["--transport", "http", "--host", "0.0.0.0"])
