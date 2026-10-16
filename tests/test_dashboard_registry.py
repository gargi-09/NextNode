"""Keeps the Features dashboard (frontend/src/features/features.json) honest.

Fails when an API route exists that the dashboard doesn't list, when the dashboard
lists a route that no longer exists, or when a tester spec can't produce a request.
Adding an endpoint therefore forces a dashboard entry in the same change.
"""
import json
import re
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app

REGISTRY = json.loads(
    (Path(__file__).resolve().parents[1] / "frontend" / "src" / "features" / "features.json")
    .read_text(encoding="utf-8"))
FEATURES = REGISTRY["features"]
STATUSES = {"live", "built", "designed", "planned"}


def _app_endpoints() -> set[str]:
    endpoints = set()
    for route in app.routes:
        if isinstance(route, APIRoute) and (route.path.startswith("/api") or route.path.startswith("/health")):
            endpoints |= {f"{m} {route.path}" for m in route.methods if m != "HEAD"}
    return endpoints


def _listed_endpoints() -> set[str]:
    return {e for f in FEATURES for e in f["endpoints"]}


def test_every_api_route_is_on_the_dashboard():
    missing = _app_endpoints() - _listed_endpoints()
    assert not missing, f"Add these routes to features.json: {sorted(missing)}"


def test_dashboard_lists_no_stale_routes():
    stale = _listed_endpoints() - _app_endpoints()
    assert not stale, f"These routes no longer exist: {sorted(stale)}"


def test_feature_entries_are_well_formed():
    ids = [f["id"] for f in FEATURES]
    assert len(ids) == len(set(ids)), "feature ids must be unique"
    for f in FEATURES:
        assert f["status"] in STATUSES, f["id"]
        assert f["category"] in REGISTRY["categories"], f["id"]
        assert f["summary"] and f["how"], f["id"]


def test_tester_specs_match_real_routes_and_inputs():
    endpoints = _app_endpoints()
    for f in (f for f in FEATURES if f.get("tryIt")):
        spec = f["tryIt"]
        assert f"{spec['method']} {spec['path']}" in endpoints, f["id"]
        inputs = {i["name"]: i for i in spec["inputs"]}
        for placeholder in re.findall(r"\{(\w+)\}", spec["path"]):
            assert inputs.get(placeholder, {}).get("in") == "path", (f["id"], placeholder)
        body = json.dumps(spec.get("body", {}))
        for placeholder in re.findall(r"\{\{(\w+)\}\}", body):
            assert inputs.get(placeholder, {}).get("in") == "body", (f["id"], placeholder)
        for i in spec["inputs"]:
            if i["type"] == "json":
                json.loads(i["default"])  # defaults must be runnable as-is
        assert f["status"] == "live", f"{f['id']}: only live features get testers"


def test_changelog_references_known_features_and_is_current():
    ids = {f["id"] for f in FEATURES}
    for entry in REGISTRY["changelog"]:
        assert set(entry["features"]) <= ids, entry["task"]
    assert REGISTRY["updated"] >= max(e["date"] for e in REGISTRY["changelog"])


def test_resolve_endpoint_is_strict():
    body = TestClient(app).get("/api/v1/ontology/resolve",
                               params={"names": "torch, Kubernetes\nJava, Pytorchh, Rust"}).json()
    by_input = {r["input"]: r for r in body["resolved"]}
    assert by_input["torch"]["canonical"] == "PyTorch" and by_input["torch"]["via"] == "alias"
    assert by_input["Kubernetes"]["via"] == "name"
    assert by_input["Java"]["canonical"] == "Java"
    assert body["unmapped"] == ["Pytorchh", "Rust"]
