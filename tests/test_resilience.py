import asyncio

import pytest
from fastapi.testclient import TestClient
from neo4j.exceptions import ServiceUnavailable

import app.api.routes as routes
from app.core.errors import NotFoundError
from app.db.neo4j import CircuitBreaker, GraphUnavailableError, Neo4jClient
from app.extraction.quota import QuotaExceeded
from app.main import app
from app.services import ingestion

client = TestClient(app)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeSession:
    def __init__(self, behaviour):
        self.behaviour = behaviour

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_read(self, fn, *args):
        return self.behaviour(fn, *args)

    execute_write = execute_read


def _client(behaviour, clock):
    c = Neo4jClient()
    c.breaker = CircuitBreaker(threshold=3, window_s=60, cooldown_s=30, clock=clock)
    c.calls = 0

    def session():
        c.calls += 1
        return FakeSession(behaviour)
    c.get_session = session
    return c


def _down(fn, *args):
    raise ServiceUnavailable("connection refused")


# ─────────────────────────── circuit breaker ───────────────────────────

def test_breaker_opens_after_threshold_and_fails_fast():
    clock = Clock()
    c = _client(_down, clock)
    for _ in range(3):
        with pytest.raises(GraphUnavailableError):
            c.read(lambda tx: None)
    assert c.breaker.state == "open"
    assert c.calls == 3

    with pytest.raises(GraphUnavailableError) as e:
        c.read(lambda tx: None)
    assert c.calls == 3                      # no network attempt while open
    assert 1 <= e.value.retry_after <= 30
    assert "console.neo4j.io" in e.value.hint


def test_breaker_half_open_probe_closes_on_success():
    clock = Clock()
    state = {"up": False}
    c = _client(lambda fn, *a: fn(None) if state["up"] else _down(fn), clock)
    for _ in range(3):
        with pytest.raises(GraphUnavailableError):
            c.read(lambda tx: None)

    clock.t += 31
    assert c.breaker.state == "half_open"
    state["up"] = True
    assert c.read(lambda tx: "ok") == "ok"
    assert c.breaker.state == "closed"


def test_failed_probe_reopens_the_breaker():
    clock = Clock()
    c = _client(_down, clock)
    for _ in range(3):
        with pytest.raises(GraphUnavailableError):
            c.read(lambda tx: None)
    clock.t += 31
    with pytest.raises(GraphUnavailableError):
        c.read(lambda tx: None)              # the probe
    assert c.breaker.state == "open"


def test_domain_errors_do_not_trip_the_breaker():
    def not_found(fn, *args):
        raise NotFoundError("User 'x' not found")
    c = _client(not_found, Clock())
    for _ in range(5):
        with pytest.raises(NotFoundError):
            c.read(lambda tx: None)
    assert c.breaker.state == "closed"


def test_old_failures_outside_the_window_are_forgotten():
    clock = Clock()
    c = _client(_down, clock)
    for _ in range(2):
        with pytest.raises(GraphUnavailableError):
            c.read(lambda tx: None)
    clock.t += 61
    with pytest.raises(GraphUnavailableError):
        c.read(lambda tx: None)
    assert c.breaker.state == "closed"


# ─────────────────────────── HTTP mapping ───────────────────────────

def test_graph_outage_returns_503_with_retry_after_and_hint(monkeypatch):
    async def down(*args, **kwargs):
        raise GraphUnavailableError(12)
    monkeypatch.setattr(routes, "analyze_gap_service", down)

    resp = client.get("/api/v1/analyze/u1/j1?explain=false")
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "12"
    assert resp.json()["detail"]["code"] == "GRAPH_UNAVAILABLE"
    assert "Resume" in resp.json()["detail"]["hint"]


def test_quota_exhaustion_returns_429_with_retry_after(monkeypatch):
    async def no_budget(*args, **kwargs):
        raise QuotaExceeded(42, "rpd")
    monkeypatch.setattr(routes, "ingest_resume", no_budget)

    resp = client.post("/api/v1/extract/resume", json={"text": "cv", "user_id": "u1"})
    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == "42"
    assert resp.json()["detail"]["code"] == "LLM_QUOTA"


# ─────────────────────────── ingestion pipeline ───────────────────────────

def test_unchanged_resume_skips_llm_and_write(monkeypatch):
    text = "Ada. Python, PyTorch."
    h = ingestion.extraction_hash("resume", text)
    monkeypatch.setattr(ingestion.catalog, "get_source_hash",
                        lambda label, node_id: {"exists": True, "source_hash": h})

    async def must_not_call(*a, **k):
        raise AssertionError("LLM must not be called for unchanged text")
    monkeypatch.setattr(ingestion, "extract_resume", must_not_call)
    monkeypatch.setattr(ingestion.writer, "write_resume",
                        lambda *a: (_ for _ in ()).throw(AssertionError("no write expected")))

    result = asyncio.run(ingestion.ingest_resume(text, "u1"))
    assert result["status"] == "unchanged"


def test_resume_ingestion_writes_only_canonical_skills(monkeypatch):
    from app.models.schemas import ExtractedResume, ExtractedSkill
    monkeypatch.setattr(ingestion.catalog, "get_source_hash",
                        lambda label, node_id: {"exists": False, "source_hash": None})

    async def fake_extract(text):
        return ExtractedResume(name="Ada", target_domain="NLP", skills=[
            ExtractedSkill(name="torch", proficiency=0.8),
            ExtractedSkill(name="Rust", proficiency=0.6),
        ]), "llm"
    monkeypatch.setattr(ingestion, "extract_resume", fake_extract)

    written = {}
    def fake_write(user_id, name, target_domain, rows, unmapped, source_hash):
        written.update(rows=rows, unmapped=unmapped, domain=target_domain, hash=source_hash)
        return True
    monkeypatch.setattr(ingestion.writer, "write_resume", fake_write)

    result = asyncio.run(ingestion.ingest_resume("cv text", "u1"))
    assert result["status"] == "created"
    assert result["skills_written"] == ["PyTorch"]
    assert result["unmapped_skills"] == ["Rust"]
    assert written["domain"] == "nlp"
    assert written["hash"] == ingestion.extraction_hash("resume", "cv text")
