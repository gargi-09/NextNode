"""Public mode: what a hosted instance exposes to strangers, and that nothing they send is kept."""
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.public as public_api
import app.services.public as public
from app.core.config import settings
from app.core.ratelimit import ClientRateLimiter
from app.main import create_app
from app.models.schemas import PUBLIC_MAX_TEXT
from conftest import FakeTx

ROOT = Path(__file__).resolve().parents[1]
RESUME = "Data analyst. Daily SQL on postgres, Python and pandas for reporting."
JOB = "Data Engineer at Northwind. Core: SQL, Apache Spark, Airflow. Preferred: Kafka."


@pytest.fixture
def public_client(monkeypatch):
    monkeypatch.setattr(public_api, "limiter", ClientRateLimiter(100, 1000))
    return TestClient(create_app(settings.model_copy(update={"public_mode": True})))


@pytest.fixture
def fake_engine(monkeypatch):
    """The services the public flow calls, recorded instead of touching Neo4j or Gemini."""
    calls = {"resume": [], "job_text": [], "job_struct": [], "analyze": [], "deleted": []}

    async def ingest_resume(text, user_id):
        calls["resume"].append(user_id)
        return {"user_id": user_id, "unmapped_skills": ["basket weaving"]}

    async def ingest_job(text, job_id, create_only=False):
        calls["job_text"].append((job_id, create_only))
        return {"job_id": job_id, "unmapped_skills": []}

    async def create_job(request):
        calls["job_struct"].append(request)
        return {"id": request.id, "unmapped_skills": []}

    async def analyze_gap(user_id, job_id, explain):
        calls["analyze"].append((user_id, job_id, explain))
        return {"user_id": user_id, "job_id": job_id, "fit_score": 0.5}

    monkeypatch.setattr(public, "ingest_resume", ingest_resume)
    monkeypatch.setattr(public, "ingest_job_description", ingest_job)
    monkeypatch.setattr(public.jobs, "create_job", create_job)
    monkeypatch.setattr(public, "analyze_gap", analyze_gap)
    monkeypatch.setattr(public.writer, "delete_user", lambda i: calls["deleted"].append(i))
    monkeypatch.setattr(public.writer, "delete_job", lambda i: calls["deleted"].append(i))
    return calls


# ─────────────────────────── what is exposed ───────────────────────────

@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v1/users"), ("GET", "/api/v1/users"), ("DELETE", "/api/v1/users/someone"),
    ("GET", "/api/v1/jobs"), ("DELETE", "/api/v1/jobs/j1"), ("POST", "/api/v1/extract/resume"),
    ("GET", "/api/v1/analyze/u1/j1"), ("POST", "/api/v1/pivot"), ("GET", "/api/v1/ontology/unmapped"),
])
def test_public_mode_hides_every_write_list_and_review_route(public_client, method, path):
    assert public_client.request(method, path, json={}).status_code == 404


def test_public_mode_serves_health_and_sample_jobs(public_client):
    assert public_client.get("/health").json() == {"status": "ok", "public_mode": True}
    ids = [j["id"] for j in public_client.get("/api/v1/public/sample-jobs").json()]
    assert ids == ["llm-platform", "data-eng", "cv-pivot"]


def test_public_cors_allows_only_the_configured_site(public_client):
    ok = public_client.options("/api/v1/public/analyze", headers={
        "Origin": "https://gargi-09.github.io", "Access-Control-Request-Method": "POST"})
    bad = public_client.options("/api/v1/public/analyze", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "https://gargi-09.github.io"
    assert "access-control-allow-origin" not in bad.headers


def test_local_mode_keeps_the_full_api():
    paths = {r.path for r in create_app(settings).routes}
    assert {"/api/v1/users", "/api/v1/jobs/{job_id}", "/api/v1/public/analyze"} <= paths


# ─────────────────────────── the flow keeps nothing ───────────────────────────

def test_a_visitor_analysis_uses_fresh_ids_and_deletes_them(public_client, fake_engine):
    r = public_client.post("/api/v1/public/analyze", json={"resume_text": RESUME, "job_text": JOB})
    assert r.status_code == 200
    body = r.json()
    assert body["stored"] is False and body["unmapped_skills"] == {"resume": ["basket weaving"], "job": []}
    user_id, job_id, explain = fake_engine["analyze"][0]
    assert user_id.startswith("pub-") and job_id.startswith("pub-") and explain is False
    assert fake_engine["job_text"] == [(job_id, True)]              # create_only: never overwrites
    assert sorted(fake_engine["deleted"]) == sorted([user_id, job_id])


def test_visitor_nodes_are_deleted_even_when_the_analysis_fails(fake_engine, monkeypatch):
    async def boom(*a):
        raise RuntimeError("graph went away")
    monkeypatch.setattr(public, "analyze_gap", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(public.analyze_public(RESUME, job_text=JOB))
    assert len(fake_engine["deleted"]) == 2 and all(i.startswith("pub-") for i in fake_engine["deleted"])


def test_two_visitors_never_share_ids(fake_engine):
    asyncio.run(public.analyze_public(RESUME, sample_job="data-eng"))
    asyncio.run(public.analyze_public(RESUME, sample_job="data-eng"))
    first, second = fake_engine["analyze"]
    assert first[0] != second[0] and first[1] != second[1]


def test_a_sample_job_is_written_as_structured_data_without_an_llm_call(public_client, fake_engine):
    r = public_client.post("/api/v1/public/analyze", json={"resume_text": RESUME, "sample_job": "cv-pivot"})
    assert r.status_code == 200 and not fake_engine["job_text"]
    assert fake_engine["job_struct"][0].title == "Computer Vision Engineer"
    assert fake_engine["job_struct"][0].id.startswith("pub-")


@pytest.mark.parametrize("body", [
    {"resume_text": RESUME},                                                  # no job at all
    {"resume_text": RESUME, "job_text": JOB, "sample_job": "data-eng"},      # both
    {"resume_text": RESUME, "sample_job": "nope"},                           # unknown sample
])
def test_exactly_one_known_job_source_is_required(public_client, fake_engine, body):
    assert public_client.post("/api/v1/public/analyze", json=body).status_code == 422
    assert not fake_engine["resume"]                                          # rejected before any work


def test_oversized_and_tiny_inputs_are_rejected(public_client, fake_engine):
    big = {"resume_text": "x" * (PUBLIC_MAX_TEXT + 1), "sample_job": "data-eng"}
    tiny = {"resume_text": "hi", "sample_job": "data-eng"}
    assert public_client.post("/api/v1/public/analyze", json=big).status_code == 422
    assert public_client.post("/api/v1/public/analyze", json=tiny).status_code == 422


def test_the_sweep_is_always_scoped_to_the_visitor_prefix():
    tx = FakeTx([[{"removed": 2}]])
    assert public.writer._sweep_tx(tx, "pub-", 3600) == 2
    query, params = tx.calls[0]
    assert "STARTS WITH $prefix" in query and params == {"prefix": "pub-", "age": 3600}
    with pytest.raises(ValueError):
        public.writer._sweep_tx(FakeTx(), "", 3600)


def test_sample_jobs_match_the_pages_demo():
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_pages
    demo = {s["id"]: s["job"] for s in build_pages.SCENARIOS}
    for key, job in public.SAMPLE_JOBS.items():
        d = demo[key]
        assert (job.title, job.company, job.domains) == (d["title"], d["company"], d["domains"])
        for ours, theirs in ((job.core_skills, d["core"]), (job.preferred_skills, d["preferred"]),
                             (job.nice_to_have_skills, d["nice"])):
            assert [[s.name, s.weight] for s in ours] == theirs


# ─────────────────────────── rate limit ───────────────────────────

def test_the_rate_limit_counts_per_minute_and_per_day():
    now = [0.0]
    rl = ClientRateLimiter(per_minute=2, per_day=3, clock=lambda: now[0])
    assert rl.acquire("a") is None and rl.acquire("a") is None
    assert rl.acquire("a") == 60 and rl.acquire("b") is None           # another client is unaffected
    now[0] = 61
    assert rl.acquire("a") is None
    now[0] = 130
    assert rl.acquire("a") == 86_400 - 130                              # 3 per day used


def test_the_route_answers_429_with_retry_after(public_client, fake_engine, monkeypatch):
    monkeypatch.setattr(public_api, "limiter", ClientRateLimiter(1, 10))
    body = {"resume_text": RESUME, "sample_job": "data-eng"}
    assert public_client.post("/api/v1/public/analyze", json=body).status_code == 200
    r = public_client.post("/api/v1/public/analyze", json=body)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "RATE_LIMITED" and "retry-after" in r.headers


def test_the_client_ip_is_the_hop_cloud_run_appended():
    class Req:
        headers = {"x-forwarded-for": "6.6.6.6, 203.0.113.7"}      # first hop is whatever the client sent
        client = type("C", (), {"host": "169.254.1.1"})()
    assert public_api.client_ip(Req()) == "203.0.113.7"


def test_the_resume_and_job_parse_together_and_a_failure_still_cleans_up(fake_engine, monkeypatch):
    order = []

    async def slow_resume(text, user_id):
        order.append("resume start"); await asyncio.sleep(0.05); order.append("resume end")
        return {"user_id": user_id, "unmapped_skills": []}

    async def failing_job(text, job_id, create_only=False):
        order.append("job start"); raise RuntimeError("Gemini unavailable")

    monkeypatch.setattr(public, "ingest_resume", slow_resume)
    monkeypatch.setattr(public, "ingest_job_description", failing_job)
    with pytest.raises(RuntimeError):
        asyncio.run(public.analyze_public(RESUME, job_text=JOB))
    assert order == ["resume start", "job start", "resume end"]       # concurrent; resume finished first
    assert len(fake_engine["deleted"]) == 2                            # cleanup ran after both settled
