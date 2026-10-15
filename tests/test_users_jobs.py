import asyncio

import pytest
from fastapi.testclient import TestClient

from conftest import FakeTx
from app.core.errors import ConflictError, NotFoundError
from app.graph import catalog, writer
from app.main import app
from app.models.schemas import JobCreate, RequiredSkillInput
from app.services import ingestion
from app.services import jobs as jobs_service
from app.services import users as users_service

client = TestClient(app)


# ─────────────────────────── routes ───────────────────────────

def test_create_user_returns_201_and_generates_id(monkeypatch):
    seen = {}
    async def fake_create(request):
        seen["request"] = request
        return {"id": request.id or "generated", "name": request.name, "skills": []}
    monkeypatch.setattr(users_service, "create_user", fake_create)

    resp = client.post("/api/v1/users", json={"name": "Ada"})
    assert resp.status_code == 201
    assert resp.json()["name"] == "Ada"
    assert seen["request"].id is None


def test_create_user_conflict_returns_409(monkeypatch):
    async def taken(request):
        raise ConflictError("User 'u1' already exists")
    monkeypatch.setattr(users_service, "create_user", taken)

    resp = client.post("/api/v1/users", json={"id": "u1", "name": "Ada"})
    assert resp.status_code == 409


def test_skill_proficiency_out_of_range_is_rejected():
    resp = client.put("/api/v1/users/u1/skills",
                      json={"skills": [{"name": "Python", "proficiency": 1.5}]})
    assert resp.status_code == 422


def test_get_unknown_user_returns_404(monkeypatch):
    async def missing(user_id):
        raise NotFoundError(f"User '{user_id}' not found")
    monkeypatch.setattr(users_service, "get_user", missing)

    assert client.get("/api/v1/users/nope").status_code == 404


def test_delete_job_returns_204(monkeypatch):
    async def ok(job_id):
        return None
    monkeypatch.setattr(jobs_service, "delete_job", ok)

    resp = client.delete("/api/v1/jobs/j1")
    assert resp.status_code == 204
    assert resp.content == b""


def test_delete_unknown_job_returns_404(monkeypatch):
    async def missing(job_id):
        raise NotFoundError(f"Job '{job_id}' not found")
    monkeypatch.setattr(jobs_service, "delete_job", missing)

    assert client.delete("/api/v1/jobs/nope").status_code == 404


def test_list_jobs_validates_limit():
    assert client.get("/api/v1/jobs?limit=0").status_code == 422


def test_create_structured_job_route(monkeypatch):
    async def fake_create(request):
        return {"id": request.id, "title": request.title, "skills": []}
    monkeypatch.setattr(jobs_service, "create_job", fake_create)

    resp = client.post("/api/v1/jobs", json={
        "id": "j1", "title": "ML Engineer",
        "core_skills": [{"name": "Python", "weight": 0.7}],
    })
    assert resp.status_code == 201
    assert resp.json()["id"] == "j1"


# ─────────────────────────── services ───────────────────────────

def test_create_job_resolves_skills_and_creates_only(monkeypatch):
    calls = {}
    def fake_write(job_id, meta, rows, domains, unmapped, source_hash, create_only):
        calls.update(rows=rows, domains=domains, unmapped=unmapped, create_only=create_only)
        return True
    monkeypatch.setattr(writer, "write_job", fake_write)
    monkeypatch.setattr(catalog, "get_job", lambda job_id: {"id": job_id})

    request = JobCreate(id="j1", title="MLE", domains=["NLP", "space"],
                        core_skills=[RequiredSkillInput(name="pytorch", weight=0.7),
                                     RequiredSkillInput(name="Blockchain", weight=0.5)])
    result = asyncio.run(jobs_service.create_job(request))

    assert result == {"id": "j1"}
    assert calls["create_only"] is True
    assert [r["name"] for r in calls["rows"]] == ["PyTorch"]
    assert calls["rows"][0]["weight"] == 0.7
    assert calls["unmapped"] == ["Blockchain"]
    assert calls["domains"] == ["nlp"]


def test_job_from_description_checks_id_before_calling_llm(monkeypatch):
    llm_called = False
    async def fake_extract(text):
        nonlocal llm_called
        llm_called = True
    monkeypatch.setattr(ingestion, "extract_jd", fake_extract)
    monkeypatch.setattr(catalog, "get_source_hash",
                        lambda label, node_id: {"exists": True, "source_hash": None})

    with pytest.raises(ConflictError):
        asyncio.run(jobs_service.create_job_from_description("text", "j1"))
    assert llm_called is False


def test_manual_skills_are_resolved_strictly(monkeypatch):
    calls = {}
    monkeypatch.setattr(writer, "replace_user_skills",
                        lambda user_id, rows, unmapped: calls.update(rows=rows, unmapped=unmapped))
    monkeypatch.setattr(catalog, "get_user", lambda user_id: {"id": user_id})

    from app.models.schemas import SkillInput
    asyncio.run(users_service.replace_skills("u1", [
        SkillInput(name="k8s", proficiency=0.6), SkillInput(name="Basket Weaving", proficiency=0.9),
    ]))
    assert [r["name"] for r in calls["rows"]] == ["Kubernetes"]
    assert calls["unmapped"] == ["Basket Weaving"]


# ─────────────────────────── graph transactions ───────────────────────────

def _found(value):
    return [{"found": value}]


def test_create_user_conflict_when_id_exists():
    with pytest.raises(ConflictError):
        writer._create_user_tx(FakeTx([_found(True)]), "u1", "Ada", None)


def test_create_user_links_target_domain():
    tx = FakeTx([_found(False)])
    writer._create_user_tx(tx, "u1", "Ada", "nlp")
    queries = [q for q, _ in tx.calls]
    assert any("CREATE (u:User" in q for q in queries)
    assert any("TARGETS" in q for q in queries)


def test_replace_skills_requires_existing_user():
    with pytest.raises(NotFoundError):
        writer._replace_user_skills_tx(FakeTx([_found(False)]), "u1", [], [])


def test_replace_skills_replaces_everything_as_manual():
    tx = FakeTx([_found(True)])
    rows = [{"key": "python", "name": "Python", "category": "language",
             "base_level": "foundational", "level": None, "proficiency": 0.8}]
    writer._replace_user_skills_tx(tx, "u1", rows, ["Basket Weaving"])
    queries = [q for q, _ in tx.calls]
    assert any("HAS_SKILL]->()" in q and "DELETE r" in q for q in queries)
    assert any('r.source      = "manual"' in q for q in queries)
    assert any(p.get("unmapped") == ["Basket Weaving"] for _, p in tx.calls)


def test_resume_without_domain_keeps_existing_targets():
    tx = FakeTx([_found(True)])
    writer._write_resume_tx(tx, "u1", "Ada", None, [], [], "h")
    assert not any("TARGETS" in q for q, _ in tx.calls)


def test_create_only_job_conflicts_when_id_exists():
    with pytest.raises(ConflictError):
        writer._write_job_tx(FakeTx([_found(True)]), "j1", {"title": "MLE"},
                             [], [], [], None, True)


def test_delete_unknown_node_raises_not_found():
    with pytest.raises(NotFoundError):
        writer._delete_tx(FakeTx([_found(False)]), "User", "u1", "User")


def test_get_user_sorts_skills_and_404s():
    with pytest.raises(NotFoundError):
        catalog._get_user_tx(FakeTx([[]]), "nope")

    tx = FakeTx([[{"id": "u1", "name": "Ada", "target_domain": None, "unmapped_skills": [],
                   "skills": [{"name": "SQL", "proficiency": 0.4},
                              {"name": "Python", "proficiency": 0.9}]}]])
    user = catalog._get_user_tx(tx, "u1")
    assert [s["name"] for s in user["skills"]] == ["Python", "SQL"]


def test_get_job_orders_skills_by_tier():
    tx = FakeTx([[{"id": "j1", "title": "MLE", "company": None, "seniority": None,
                   "domains": [], "unmapped_skills": [], "skills": [
        {"name": "Docker", "importance": "nice_to_have", "weight": 0.9},
        {"name": "Python", "importance": "core", "weight": 0.5},
        {"name": "PyTorch", "importance": "core", "weight": 0.8},
    ]}]])
    job = catalog._get_job_tx(tx, "j1")
    assert [s["name"] for s in job["skills"]] == ["PyTorch", "Python", "Docker"]
