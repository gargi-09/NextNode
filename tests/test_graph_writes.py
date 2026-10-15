import re

import pytest

from conftest import FakeTx
from app.core.errors import NotFoundError
from app.graph import writer, reasoner, pivot
from app.graph.normalize import owned_skill_rows, required_skill_rows, resolve_domains
from app.models.schemas import ExtractedJD, ExtractedResume, ExtractedSkill


def _skill(name, proficiency, level="intermediate", category="tool"):
    return ExtractedSkill(name=name, category=category, level=level, proficiency=proficiency)


JD = ExtractedJD(
    title="ML Engineer", company="Acme", seniority="mid",
    core_skills=[_skill("Python", 0.7), _skill("PyTorch", 0.6)],
    preferred_skills=[_skill("python", 0.9), _skill("Docker", 0.4)],
    nice_to_have_skills=[_skill(" Docker ", 0.2), _skill("torch", 0.9),
                         _skill("Quantum Basket Weaving", 0.5)],
    domains=["NLP", "nlp", "ML Infrastructure", "underwater robotics"],
)

RESUME = ExtractedResume(
    name="Test",
    skills=[_skill("python", 0.6), _skill("Python", 0.9), _skill("SQL", 0.5),
            _skill("sklearn", 0.7), _skill("Rust", 0.5)],
    target_domain="nlp",
)


def _found(value):
    return [{"found": value}]


# ── strict canonical resolution ──

def test_required_rows_resolve_aliases_and_keep_tier_precedence():
    rows, unmapped = required_skill_rows(JD)
    by_name = {r["name"]: r for r in rows}

    assert set(by_name) == {"Python", "PyTorch", "Docker"}  # "torch" folded into PyTorch
    assert by_name["Python"]["importance"] == "core"
    assert by_name["Python"]["weight"] == 0.7
    assert by_name["PyTorch"]["importance"] == "core"
    assert by_name["Docker"]["importance"] == "preferred"
    assert unmapped == ["Quantum Basket Weaving"]


def test_category_comes_from_the_ontology_not_the_input():
    rows, _ = required_skill_rows(JD)
    python = next(r for r in rows if r["name"] == "Python")
    assert python["category"] == "language"   # input said "tool"
    assert python["key"] == "python"


def test_owned_rows_canonicalize_and_report_unknowns():
    rows, unmapped = owned_skill_rows(RESUME.skills)
    by_name = {r["name"]: r for r in rows}
    assert by_name["Python"]["proficiency"] == 0.9
    assert "scikit-learn" in by_name
    assert unmapped == ["Rust"]


def test_domains_resolve_to_keys_and_drop_unknowns():
    keys, unknown = resolve_domains(JD.domains)
    assert keys == ["nlp", "ml_infrastructure"]
    assert unknown == ["underwater robotics"]


# ── G1: relationships merged on identity, single transaction, replace semantics ──

def _merges_on_identity_only(query):
    # no property map inside a relationship pattern, e.g. [:REQUIRES_SKILL {weight: ...}]
    return not re.search(r"MERGE[^\n]*\[[^\]]*\{", query)


def test_write_job_merges_relationships_on_identity():
    rows, unmapped = required_skill_rows(JD)
    tx = FakeTx([_found(False)])
    created = writer._write_job_tx(tx, "job_1", {"title": "MLE"}, rows, ["nlp"],
                                   unmapped, "hash123", False)
    queries = [q for q, _ in tx.calls]

    assert created is True
    assert all(_merges_on_identity_only(q) for q in queries)
    assert any("DELETE r" in q and "REQUIRES_SKILL" in q for q in queries)
    meta_params = next(p for q, p in tx.calls if "source_hash" in p)
    assert meta_params["unmapped"] == ["Quantum Basket Weaving"]
    assert meta_params["source_hash"] == "hash123"
    assert len(next(p["rows"] for q, p in tx.calls if "rows" in p)) == 3


def test_write_resume_replaces_resume_skills_and_keeps_manual():
    rows, unmapped = owned_skill_rows(RESUME.skills)
    tx = FakeTx([_found(True)])
    created = writer._write_resume_tx(tx, "user_1", "Test", "nlp", rows, unmapped, "h")
    queries = [q for q, _ in tx.calls]

    assert created is False
    assert all(_merges_on_identity_only(q) for q in queries)
    assert any('HAS_SKILL {source: "resume"}' in q and "DELETE r" in q for q in queries)
    assert any('WHERE r.source = "resume"' in q for q in queries)
    assert any("TARGETS" in q and "DELETE old" in q for q in queries)


def test_skill_nodes_only_take_values_from_rows():
    # every SET on a Skill node reads from `row.` (ontology data), never from parameters
    for line in writer._UPSERT_SKILL.splitlines():
        if "s." in line and "=" in line:
            assert "row." in line


# ── G2 / G3 / G4: reasoner queries ──

def _meta(user=True, job=True):
    return [{"user_exists": user, "job_exists": job,
             "name": "Test", "title": "ML Engineer", "company": "Acme"}]


def test_missing_job_raises_not_found():
    with pytest.raises(NotFoundError):
        reasoner._fetch_gap_inputs_tx(FakeTx([_meta(job=False)]), "u", "j")


def test_missing_user_raises_not_found():
    with pytest.raises(NotFoundError):
        reasoner._fetch_gap_inputs_tx(FakeTx([_meta(user=False)]), "u", "j")


def test_domain_bonus_requires_shared_domain_and_prereqs_are_distinct():
    tx = FakeTx([_meta(), [], [{"shared_domains": 1}], [{"skill": "X", "prereqs_owned": 2}]])
    inputs = reasoner._fetch_gap_inputs_tx(tx, "u", "j")

    domain_q = tx.calls[2][0]
    assert re.search(r"\[:TARGETS\]->\(d:Domain\)\s*<-\[:IN_DOMAIN\]-", domain_q)
    assert "count(DISTINCT d)" in domain_q
    assert "count(DISTINCT owned)" in tx.calls[3][0]
    assert inputs["shared_domains"] == 1
    assert inputs["prereq_coverage"] == {"X": 2}
    assert inputs["job_title"] == "ML Engineer"


def test_gap_inputs_include_real_prerequisite_edges():
    path = {"owned": "Python", "proficiency": 0.9, "target": "PyTorch",
            "edges": [{"source": "Python", "target": "NumPy", "strength": 1.0},
                      {"source": "NumPy", "target": "PyTorch", "strength": 0.9}]}
    tx = FakeTx([_meta(), [], [{"shared_domains": 0}], [], [path],
                 [{"source": "Python", "target": "LangChain", "strength": 0.7}],
                 [{"user_domains": ["nlp"], "job_domains": ["nlp", "ml_infrastructure"]}]])
    inputs = reasoner._fetch_gap_inputs_tx(tx, "u", "j")

    assert "PREREQUISITE_OF*1..3" in tx.calls[4][0]
    assert inputs["prereq_paths"] == [path]
    assert inputs["required_prereq_edges"][0]["target"] == "LangChain"
    assert inputs["job_domains"] == ["nlp", "ml_infrastructure"]


def test_pivot_unknown_job_raises_not_found():
    tx = FakeTx([[{"user_exists": True, "job_exists": False}]])
    with pytest.raises(NotFoundError):
        pivot._fetch_pivot_inputs_tx(tx, "u", "missing_job")
