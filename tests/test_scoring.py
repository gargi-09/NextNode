import pytest

from app.graph.scoring import score_gap, build_roadmap, score_pivot
from app.models.schemas import ExtractedPivotRole, PivotSkill


def _row(skill, importance, weight, proficiency, status, category="tool"):
    return {
        "skill": skill, "category": category, "importance": importance,
        "required_weight": weight, "your_proficiency": proficiency, "status": status,
    }


ROWS = [
    _row("A", "core",         0.8, 0.9, "met"),
    _row("B", "core",         0.6, 0.0, "missing"),
    _row("C", "preferred",    0.5, 0.2, "partial"),
    _row("D", "nice_to_have", 0.4, 0.0, "missing"),
]


def test_score_gap_golden():
    result = score_gap("u1", "j1", ROWS, shared_domains=1, prereq_coverage={"B": 1})

    # B: 0.6 * (1 - 0.15) = 0.51 | C: 0.5*0.5*0.6 = 0.15 | D: 0.4*0.2 = 0.08
    assert result["gap_score"] == pytest.approx(0.74)
    # 1 - 0.74/1.78 + 0.025
    assert result["fit_score"] == 0.609
    assert result["domain_bonus"] == 0.025

    by_skill = {s["skill"]: s for s in result["skill_breakdown"]}
    assert by_skill["B"]["gap_contribution"] == 0.51
    assert by_skill["B"]["prereqs_owned"] == 1
    assert by_skill["A"]["gap_contribution"] == 0.0

    roadmap = result["roadmap"]
    assert [r["skill"] for r in roadmap] == ["B", "C", "D"]
    assert [r["priority"] for r in roadmap] == ["medium", "medium", "low"]
    assert roadmap[0]["reason"].startswith("Not in your profile but you own 1 prerequisite")
    assert roadmap[1]["reason"] == "You have this at 20% but 50% is expected"


def test_domain_bonus_is_capped():
    assert score_gap("u", "j", ROWS, 3, {})["domain_bonus"] == 0.05


def test_no_shared_domains_means_no_bonus():
    assert score_gap("u", "j", ROWS, 0, {})["domain_bonus"] == 0.0


def test_prereq_reduction_capped_at_40_percent():
    rows = [_row("X", "core", 1.0, 0.0, "missing")]
    result = score_gap("u", "j", rows, 0, {"X": 10})
    assert result["gap_score"] == pytest.approx(0.6)


def test_core_missing_without_prereqs_is_high_priority():
    roadmap = build_roadmap([{
        "skill": "K8s", "importance": "core", "status": "missing",
        "prereqs_owned": 0, "gap_contribution": 0.5,
        "your_proficiency": 0.0, "required_weight": 0.5,
    }])
    assert roadmap[0]["priority"] == "high"
    assert roadmap[0]["reason"] == "Core requirement — not in your profile, address first"


def _pskill(name, proficiency, transferability):
    return PivotSkill(name=name, category="tool", level="intermediate",
                      proficiency=proficiency, transferability=transferability)


def test_score_pivot_golden():
    role = ExtractedPivotRole(
        role_title="Research Scientist",
        core_skills=[_pskill("X", 0.8, 0.5)],
        preferred_skills=[_pskill("Y", 0.5, 0.9)],
        domains=["nlp"],
    )
    result = score_pivot("u1", role, user_skills={"Y": 0.6}, jd_skills={})

    # X missing: 0.8 * (1 - 0.5*0.4) = 0.64 ; max = 0.8 + 0.5*0.6 = 1.1
    assert result["pivot_score"] == 0.418
    assert result["effort"] == "high"
    assert result["transferable_skills"] == ["Y"]
    assert [g["skill"] for g in result["skill_gaps"]] == ["X"]
    assert result["role_overlap_pct"] is None
    assert result["jd_context_used"] is False
