import pytest
from fastapi.testclient import TestClient

from app.core.errors import NotFoundError
from app.main import app
import app.api.routes as routes

client = TestClient(app)


def test_unknown_entities_return_404(monkeypatch):
    async def not_found(*args, **kwargs):
        raise NotFoundError("Job 'nope' not found")
    monkeypatch.setattr(routes, "analyze_gap_service", not_found)

    resp = client.get("/api/v1/analyze/u1/nope")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Job 'nope' not found"


def test_internal_errors_do_not_leak_details(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("bolt://secret-host refused connection")
    monkeypatch.setattr(routes, "analyze_pivot_service", boom)

    resp = client.post("/api/v1/pivot", json={"user_id": "u1", "target_role": "MLE"})
    assert resp.status_code == 500
    assert "secret-host" not in resp.text


def test_analyze_rejects_untyped_service_output(monkeypatch):
    # the response model is enforced: a partial dict is a server bug, not a valid answer
    async def partial(user_id, job_id, explain):
        return {"user_id": user_id, "job_id": job_id, "fit_score": 0.5}
    monkeypatch.setattr(routes, "analyze_gap_service", partial)

    with pytest.raises(Exception):
        client.get("/api/v1/analyze/u1/j1?explain=false")
