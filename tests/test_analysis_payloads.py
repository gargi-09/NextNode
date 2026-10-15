"""End-to-end /analyze with a faked graph read: real scoring, real payload builders,
real response model. Scenario matches the manual Swagger test plan."""
from fastapi.testclient import TestClient

from app.main import app
from app.models.analysis import AnalysisResponse
from app.services import analysis as analysis_service

client = TestClient(app)


def _row(skill, importance, weight, proficiency, status):
    return {"skill": skill, "category": None, "importance": importance,
            "required_weight": weight, "your_proficiency": proficiency, "status": status}


def _edge(a, b, s):
    return {"source": a, "target": b, "strength": s}


INPUTS = {
    "rows": [
        _row("Python", "core", 0.7, 0.9, "met"),
        _row("PyTorch", "core", 0.7, 0.4, "partial"),
        _row("LLM fine-tuning", "core", 0.6, 0.0, "missing"),
        _row("Vector Databases", "preferred", 0.5, 0.0, "missing"),
        _row("Docker", "nice_to_have", 0.4, 0.0, "missing"),
    ],
    "shared_domains": 1,
    "prereq_coverage": {"LLM fine-tuning": 2, "Vector Databases": 2, "Docker": 0},
    "user_name": "Test User", "job_title": "ML Engineer", "company": "Acme",
    "prereq_paths": [
        {"owned": "PyTorch", "proficiency": 0.4, "target": "LLM fine-tuning",
         "edges": [_edge("PyTorch", "LLM fine-tuning", 0.8)]},
        {"owned": "Python", "proficiency": 0.9, "target": "LLM fine-tuning",
         "edges": [_edge("Python", "NumPy", 1.0), _edge("NumPy", "PyTorch", 0.9),
                   _edge("PyTorch", "LLM fine-tuning", 0.8)]},
        {"owned": "Elasticsearch", "proficiency": 0.8, "target": "Vector Databases",
         "edges": [_edge("Elasticsearch", "Vector Databases", 0.7)]},
        {"owned": "Python", "proficiency": 0.9, "target": "Vector Databases",
         "edges": [_edge("Python", "Vector Databases", 0.6)]},
    ],
    "required_prereq_edges": [],
    "user_domains": ["nlp"],
    "job_domains": ["nlp"],
}


def _analyze(monkeypatch):
    monkeypatch.setattr(analysis_service, "fetch_gap_inputs", lambda u, j: INPUTS)
    resp = client.get("/api/v1/analyze/u_test/j_test?explain=false")
    assert resp.status_code == 200
    return resp.json()


def test_scores_match_the_manual_test_plan(monkeypatch):
    body = _analyze(monkeypatch)
    AnalysisResponse(**body)  # round-trips through the typed schema
    assert body["fit_score"] == 0.58
    assert body["gap_score"] == 1.06
    assert body["explanation_status"] == "not_requested"
    assert body["scoring_version"] == "1"
    assert [s["skill"] for s in body["roadmap"]] == [
        "LLM fine-tuning", "PyTorch", "Vector Databases", "Docker"]


def test_force_graph_links_reference_existing_nodes(monkeypatch):
    graph = _analyze(monkeypatch)["graph"]
    ids = [n["id"] for n in graph["nodes"]]

    assert len(ids) == len(set(ids)), "node ids must be unique"
    for link in graph["links"]:
        assert link["source"] in ids and link["target"] in ids, link

    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id["skill:NumPy"]["status"] == "prerequisite"   # intermediate hop, not owned
    assert by_id["skill:Elasticsearch"]["status"] == "owned"
    assert by_id["skill:PyTorch"]["status"] == "partial"
    assert by_id["domain:nlp"]["type"] == "domain"

    types = {(l["source"], l["target"], l["type"]) for l in graph["links"]}
    assert ("skill:NumPy", "skill:PyTorch", "PREREQUISITE_OF") in types
    assert ("user:u_test", "skill:Elasticsearch", "HAS_SKILL") in types
    assert ("job:j_test", "skill:Docker", "REQUIRES_SKILL") in types
    assert ("user:u_test", "domain:nlp", "TARGETS") in types
    assert ("job:j_test", "domain:nlp", "IN_DOMAIN") in types
    # missing skills have no HAS_SKILL edge
    assert ("user:u_test", "skill:Docker", "HAS_SKILL") not in types


def test_roadmap_flow_is_ready_for_xyflow(monkeypatch):
    flow = _analyze(monkeypatch)["roadmap_flow"]
    node_ids = {n["id"] for n in flow["nodes"]}

    for node in flow["nodes"]:
        assert set(node["position"]) == {"x", "y"}
        assert node["type"] in ("roadmapStep", "ownedSkill")
        assert node["data"]["label"]
    for edge in flow["edges"]:
        assert edge["source"] in node_ids and edge["target"] in node_ids
    assert len({e["id"] for e in flow["edges"]}) == len(flow["edges"])

    steps = [n for n in flow["nodes"] if n["type"] == "roadmapStep"]
    assert [s["data"]["rank"] for s in steps] == [1, 2, 3, 4]
    assert [s["position"]["y"] for s in steps] == sorted(s["position"]["y"] for s in steps)

    unlocks = {(e["source"], e["target"]) for e in flow["edges"] if e["data"]["kind"] == "unlocks"}
    assert ("owned:Python", "step:LLM fine-tuning") in unlocks
    assert ("owned:Elasticsearch", "step:Vector Databases") in unlocks
    owned_ys = [n["position"]["y"] for n in flow["nodes"] if n["type"] == "ownedSkill"]
    assert len(owned_ys) == len(set(owned_ys)), "owned skill nodes must not overlap"


def test_explanation_quota_does_not_fail_the_analysis(monkeypatch):
    from app.extraction.quota import QuotaExceeded

    async def no_budget(**kwargs):
        raise QuotaExceeded(3600, "explain_budget")
    monkeypatch.setattr(analysis_service, "explain_gap", no_budget)
    monkeypatch.setattr(analysis_service, "fetch_gap_inputs", lambda u, j: INPUTS)

    body = client.get("/api/v1/analyze/u_test/j_test").json()
    assert body["explanation"] is None
    assert body["explanation_status"] == "skipped_quota"
    assert body["fit_score"] == 0.58


def test_ontology_graph_endpoint_is_self_consistent():
    body = client.get("/api/v1/ontology/graph").json()
    ids = {n["id"] for n in body["nodes"]}
    assert len(ids) == len(body["nodes"])
    for link in body["links"]:
        assert link["source"] in ids and link["target"] in ids
    assert any(l["type"] == "PREREQUISITE_OF" for l in body["links"])
