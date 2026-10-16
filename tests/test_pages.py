"""The static demo pages (docs/) must match the registry, ontology and scoring code."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.graph.ontology import get_ontology
from app.graph.scoring import score_gap
from app.main import app

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def test_generated_data_is_up_to_date():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_pages.py"), "--check"],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_known_scenario_matches_the_manual_test_plan():
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_pages

    scenario = build_pages.build_data()["scenarios"][0]
    assert scenario["expected"]["fit_score"] == 0.528   # same numbers as the demo-data walkthrough
    assert scenario["expected"]["roadmap"][2] == "PyTorch"


def test_pages_reference_existing_assets():
    for page in ("index.html", "sandbox.html"):
        html = (DOCS / page).read_text(encoding="utf-8")
        for asset in ("assets/site.css", "assets/data.js", "assets/nextnode-core.js"):
            assert asset in html, (page, asset)
            assert (DOCS / asset).is_file()
    sandbox = (DOCS / "sandbox.html").read_text(encoding="utf-8")
    for asset in ("assets/mock-api.js", "assets/viz.js"):
        assert asset in sandbox and (DOCS / asset).is_file(), asset


def test_api_serves_the_demo_pages():
    client = TestClient(app)
    for path in ("/demo/", "/demo/sandbox.html", "/demo/assets/data.js"):
        assert client.get(path).status_code == 200, path


# ── the in-browser mock API, run under Node, against the Python engine ──

NODE_SMOKE = r"""
const vm = require("vm"), fs = require("fs"), path = require("path");
globalThis.window = globalThis;
for (const f of ["data.js", "nextnode-core.js", "mock-api.js"])
  vm.runInThisContext(fs.readFileSync(path.join(process.argv[2], f), "utf8"));
(async () => {
  const r = (m, p, o) => NNMock.request(m, p, o);
  const out = {};
  out.create = (await r("POST", "/api/v1/users", { body: { id: "u1", name: "Ada", target_domain: "NLP" } })).status;
  out.dup = (await r("POST", "/api/v1/users", { body: { id: "u1", name: "Ada" } })).status;
  out.bad = (await r("PUT", "/api/v1/users/u1/skills", { body: { skills: [{ name: "Python", proficiency: 1.5 }] } })).status;
  const skills = await r("PUT", "/api/v1/users/u1/skills", { body: { skills: [
    { name: "python", proficiency: 0.9 }, { name: "torch", proficiency: 0.5 }, { name: "Elasticsearch", proficiency: 0.8 },
    { name: "k8s", proficiency: 0.4 }, { name: "Basket Weaving", proficiency: 0.7 }] } });
  out.unmapped = skills.data.unmapped_skills;
  out.job = (await r("POST", "/api/v1/jobs", { body: { id: "j1", title: "MLE", domains: ["nlp"],
    core_skills: [{ name: "Python", weight: 0.7 }, { name: "PyTorch", weight: 0.7 }, { name: "LLM fine-tuning", weight: 0.6 }],
    preferred_skills: [{ name: "vector db", weight: 0.5 }, { name: "RAG", weight: 0.5 }],
    nice_to_have_skills: [{ name: "Docker", weight: 0.4 }] } })).status;
  const a = await r("GET", "/api/v1/analyze/u1/j1", { query: { explain: false } });
  out.fit = a.data.fit_score; out.gap = a.data.gap_score; out.roadmap = a.data.roadmap.map(s => s.skill);
  out.graphOk = a.data.graph.links.every(l => a.data.graph.nodes.some(n => n.id === l.source) && a.data.graph.nodes.some(n => n.id === l.target));
  out.missing = (await r("GET", "/api/v1/analyze/u1/nope", {})).status;
  const res1 = await r("POST", "/api/v1/users/u1/resume", { body: { text: "Ada. Python and PyTorch, some Docker." } });
  const res2 = await r("POST", "/api/v1/users/u1/resume", { body: { text: "Ada.  Python and PyTorch,   some Docker." } });
  out.resume = [res1.data.status, res2.data.status, res1.data.skills_written];
  out.pivot = (await r("POST", "/api/v1/pivot", { body: { user_id: "u1", target_role: "mlops engineer" } })).data.pivot_score;
  out.del = (await r("DELETE", "/api/v1/users/u1", {})).status;
  out.unrouted = [];
  for (const f of window.NEXTNODE.features) for (const e of f.endpoints) {
    const [m, p] = e.split(" ");
    const resp = await r(m, p.replace(/\{\w+\}/g, "x"), { query: { names: "x" }, body: {} });
    if (resp.status === 404 && resp.data.detail === "Not Found") out.unrouted.push(e);
  }
  console.log(JSON.stringify(out));
})();
"""


def _python_expected():
    o = get_ontology()
    owned = {"Python": 0.9, "PyTorch": 0.5, "Elasticsearch": 0.8, "Kubernetes": 0.4}
    job = [("Python", "core", 0.7), ("PyTorch", "core", 0.7), ("LLM fine-tuning", "core", 0.6),
           ("Vector Databases", "preferred", 0.5), ("Retrieval-Augmented Generation", "preferred", 0.5),
           ("Docker", "nice_to_have", 0.4)]
    nxt = {}
    for p in o.prerequisites:
        nxt.setdefault(p.source, set()).add(p.target)

    def reach(src):
        out, frontier = set(), {src}
        for _ in range(3):
            frontier = {t for f in frontier for t in nxt.get(f, ())}
            out |= frontier
        return out

    rows = []
    for name, imp, w in job:
        p = owned.get(name)
        rows.append({"skill": name, "category": None, "importance": imp, "required_weight": w,
                     "your_proficiency": p or 0.0,
                     "status": "missing" if p is None else ("met" if p >= w else "partial")})
    prereq = {r["skill"]: sum(1 for s in owned if r["skill"] in reach(s)) for r in rows if r["status"] == "missing"}
    return score_gap("u1", "j1", rows, 1, prereq)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_mock_api_matches_the_python_engine(tmp_path):
    script = tmp_path / "smoke.js"
    script.write_text(NODE_SMOKE, encoding="utf-8")
    result = subprocess.run(["node", str(script), str(DOCS / "assets")], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    expected = _python_expected()

    assert (out["create"], out["dup"], out["bad"], out["job"], out["missing"], out["del"]) == (201, 409, 422, 201, 404, 204)
    assert out["unmapped"] == ["Basket Weaving"]
    assert abs(out["fit"] - expected["fit_score"]) < 0.0015
    assert abs(out["gap"] - expected["gap_score"]) < 0.0015
    assert out["roadmap"] == [s["skill"] for s in expected["roadmap"]]
    assert out["graphOk"] is True
    assert out["resume"][0] == "updated" and out["resume"][1] == "unchanged"
    assert set(out["resume"][2]) == {"Python", "PyTorch", "Docker"}
    assert 0 <= out["pivot"] <= 1
    assert out["unrouted"] == [], f"add these routes to docs/assets/mock-api.js: {out['unrouted']}"
