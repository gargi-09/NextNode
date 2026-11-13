"""Any-of requirements: "a language such as Python, Java, or C++" is ONE requirement.

Before scoring version 2 each alternative was its own core skill, so a Python-only resume was
"missing" Java and C++ (found on a real new-grad posting, 2026-11-13).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.extraction import prompts
from app.graph import writer
from app.graph.normalize import required_skill_rows
from app.graph.scoring import SCORING_VERSION, collapse_alternatives, score_gap
from app.models.schemas import ExtractedJD, ExtractedSkill, RequiredSkillInput
from app.services.jobs import _to_extracted
from conftest import FakeTx

ROOT = Path(__file__).resolve().parents[1]


def jd(core=(), preferred=()):
    return ExtractedJD(title="SWE", core_skills=list(core), preferred_skills=list(preferred), nice_to_have_skills=[])


def skill(name, w=0.3, alts=()):
    return ExtractedSkill(name=name, proficiency=w, alternatives=list(alts))


def row(name, status, p=0.0, w=0.3, importance="core", group=None, rank=None):
    return {"skill": name, "category": "language", "importance": importance, "required_weight": w,
            "your_proficiency": p, "status": status, "group": group, "alt_rank": rank}


# ─────────────────────────── parsing to rows ───────────────────────────

def test_alternatives_become_one_group_in_listed_order():
    rows, unmapped = required_skill_rows(jd([skill("python", alts=["Java", "c++"]), skill("Data Structures and Algorithms")]))
    grouped = [r for r in rows if r.get("group")]
    assert [(r["name"], r["alt_rank"]) for r in grouped] == [("Python", 0), ("Java", 1), ("C++", 2)]
    assert len({r["group"] for r in grouped}) == 1 and not unmapped
    assert all(r["importance"] == "core" and r["weight"] == 0.3 for r in grouped)


def test_a_group_with_one_known_skill_is_a_plain_requirement():
    rows, unmapped = required_skill_rows(jd([skill("Python", alts=["Brainfuck"])]))
    assert [(r["name"], r.get("group")) for r in rows] == [("Python", None)] and unmapped == ["Brainfuck"]


def test_a_skill_required_on_its_own_covers_the_group():
    rows, _ = required_skill_rows(jd([skill("Java", alts=["Python"]), skill("Python", 0.6)]))
    assert [(r["name"], r.get("group"), r["weight"]) for r in rows] == [("Python", None, 0.6)]


def test_structured_job_input_and_the_prompt_carry_alternatives():
    extracted = _to_extracted([RequiredSkillInput(name="Python", weight=0.4, alternatives=["Java"])])
    assert extracted[0].alternatives == ["Java"]
    assert "alternatives" in prompts.JD_EXTRACTION_PROMPT and prompts.PROMPT_VERSION == "3"


def test_the_group_is_stored_on_the_requirement_edge():
    tx = FakeTx([[{"found": False}]])
    writer._write_job_tx(tx, "j", {"title": "SWE"}, [{"key": "python", "name": "Python", "category": "language",
                         "base_level": "x", "level": None, "importance": "core", "weight": 0.3,
                         "group": "g1", "alt_rank": 0}], [], [], None, False)
    query = next(q for q, _ in tx.calls if "REQUIRES_SKILL]->(s)" in q)
    assert "r.group      = row.group" in query and "r.alt_rank   = row.alt_rank" in query


# ─────────────────────────── scoring ───────────────────────────

def test_the_best_member_stands_for_the_group():
    rows = [row("Java", "missing", group="g1", rank=1), row("Python", "met", 0.8, group="g1", rank=0),
            row("C++", "partial", 0.2, group="g1", rank=2), row("SQL", "missing")]
    out = collapse_alternatives(rows, {})
    assert [(r["skill"], r["status"], r["alternatives"]) for r in out] == [
        ("Python", "met", ["Java", "C++"]), ("SQL", "missing", [])]
    assert all("group" not in r and "alt_rank" not in r for r in out)


def test_with_nothing_owned_the_alternative_closest_by_prerequisites_wins():
    rows = [row("Java", "missing", group="g1", rank=0), row("PyTorch", "missing", group="g1", rank=1)]
    assert collapse_alternatives(rows, {"PyTorch": 2})[0]["skill"] == "PyTorch"
    assert collapse_alternatives(rows, {})[0]["skill"] == "Java"            # tie: listed first


def test_a_python_only_candidate_now_meets_the_language_requirement():
    rows = [row("Python", "met", 0.9, group="g1", rank=0), row("Java", "missing", group="g1", rank=1),
            row("C++", "missing", group="g1", rank=2), row("Data Structures and Algorithms", "missing")]
    flat = score_gap("u", "j", [{k: v for k, v in r.items() if k not in ("group", "alt_rank")} for r in rows], 0, {})
    grouped = score_gap("u", "j", collapse_alternatives(rows, {}), 0, {})
    assert flat["fit_score"] == 0.25 and grouped["fit_score"] == 0.5     # 1 of 4 met before, 1 of 2 now
    assert [s["skill"] for s in grouped["roadmap"]] == ["Data Structures and Algorithms"]
    assert SCORING_VERSION == "2"


def test_a_missing_group_is_one_roadmap_step_that_names_the_alternatives():
    rows = [row("Python", "missing", group="g1", rank=0), row("Java", "missing", group="g1", rank=1)]
    step = score_gap("u", "j", collapse_alternatives(rows, {}), 0, {})["roadmap"]
    assert len(step) == 1 and step[0]["alternatives"] == ["Java"] and "(or Java: any one counts)" in step[0]["reason"]


def test_jobs_without_groups_score_exactly_as_before():
    rows = [row("Python", "partial", 0.5, 0.7), row("SQL", "met", 0.8, 0.6), row("Docker", "missing", 0, 0.4, "preferred")]
    plain = [{k: v for k, v in r.items() if k not in ("group", "alt_rank")} for r in rows]
    before = score_gap("u", "j", plain, 1, {"Docker": 1})
    after = score_gap("u", "j", collapse_alternatives(rows, {"Docker": 1}), 1, {"Docker": 1})
    assert (after["fit_score"], after["gap_score"]) == (before["fit_score"], before["gap_score"])


# ─────────────────────────── the JavaScript port agrees ───────────────────────────

PARITY = r"""
const fs = require("fs"); global.window = {};
for (const f of ["data.js", "nextnode-core.js"]) eval(fs.readFileSync(process.argv[2] + "/" + f, "utf8"));
const NN = window.NN;
const reqs = [
  { skill: "Python", importance: "core", weight: 0.3, group: "g1", alt_rank: 0 },
  { skill: "Java", importance: "core", weight: 0.3, group: "g1", alt_rank: 1 },
  { skill: "PyTorch", importance: "core", weight: 0.5 },
  { skill: "Docker", importance: "preferred", weight: 0.4, group: "g2", alt_rank: 0 },
  { skill: "Kubernetes", importance: "preferred", weight: 0.4, group: "g2", alt_rank: 1 },
];
const a = NN.analyzeProfile({ Python: 0.9, NumPy: 0.6 }, reqs, null, []);
console.log(JSON.stringify({ fit: a.result.fit_score, gap: a.result.gap_score,
  rows: a.result.skill_breakdown.map(s => [s.skill, s.status, s.alternatives]),
  roadmap: a.result.roadmap.map(s => [s.skill, s.reason]) }));
"""


def test_the_javascript_port_scores_groups_like_python(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("needs Node")
    script = tmp_path / "parity.js"
    script.write_text(PARITY, encoding="utf-8")
    proc = subprocess.run([node, str(script), str(ROOT / "docs" / "assets")], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    js = json.loads(proc.stdout)

    from app.graph.ontology import get_ontology
    o = get_ontology()
    nxt = {}
    for p in o.prerequisites:
        nxt.setdefault(p.source, set()).add(p.target)

    def reach(src):
        out, frontier = set(), {src}
        for _ in range(3):
            frontier = {t for f in frontier for t in nxt.get(f, ())}
            out |= frontier
        return out

    owned = {"Python": 0.9, "NumPy": 0.6}
    reqs = [("Python", "core", 0.3, "g1", 0), ("Java", "core", 0.3, "g1", 1), ("PyTorch", "core", 0.5, None, None),
            ("Docker", "preferred", 0.4, "g2", 0), ("Kubernetes", "preferred", 0.4, "g2", 1)]
    rows = [row(n, "missing" if n not in owned else ("met" if owned[n] >= w else "partial"), owned.get(n, 0.0), w, imp, g, r)
            for n, imp, w, g, r in reqs]
    prereq = {r["skill"]: sum(1 for s in owned if r["skill"] in reach(s)) for r in rows if r["status"] == "missing"}
    py = score_gap("u", "j", collapse_alternatives(rows, prereq), 0, prereq)
    assert (js["fit"], js["gap"]) == (py["fit_score"], py["gap_score"])
    assert js["rows"] == [[s["skill"], s["status"], s["alternatives"]] for s in py["skill_breakdown"]]
    assert js["roadmap"] == [[s["skill"], s["reason"]] for s in py["roadmap"]]
