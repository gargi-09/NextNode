"""Pure helpers that turn extractions / user input into canonical write rows.

Strict mode: every skill is resolved against the ontology. Only canonical skills
become rows; anything unknown is returned in `unmapped` and stored as a plain
list property on the owning User / JobPosting — never as a new Skill node.
Rows are deduplicated by canonical key, so "torch" and "PyTorch" collapse to one.
"""
from app.graph.ontology import Ontology, get_ontology
from app.models.schemas import ExtractedJD

# earlier tier wins when a skill appears in several
IMPORTANCE_TIERS = ("core", "preferred", "nice_to_have")

MAX_UNMAPPED = 50


def _collect_unmapped(unmapped: list[str], name: str):
    if name not in unmapped and len(unmapped) < MAX_UNMAPPED:
        unmapped.append(name)


def _base_row(skill, canonical) -> dict:
    return {
        "key":        canonical.key,
        "name":       canonical.name,      # canonical spelling, never the raw input
        "category":   canonical.category,  # from the ontology, never from the LLM
        "base_level": canonical.level,
        "level":      skill.level,         # context-dependent level → stored on the edge
    }


def required_skill_rows(extracted: ExtractedJD, ontology: Ontology | None = None
                        ) -> tuple[list[dict], list[str]]:
    ontology = ontology or get_ontology()
    tiers = {
        "core":         extracted.core_skills,
        "preferred":    extracted.preferred_skills,
        "nice_to_have": extracted.nice_to_have_skills,
    }
    rows: dict[str, dict] = {}
    unmapped: list[str] = []
    for importance in IMPORTANCE_TIERS:
        for skill in tiers[importance]:
            name = skill.name.strip()
            if not name:
                continue
            canonical = ontology.resolve(name)
            if canonical is None:
                _collect_unmapped(unmapped, name)
                continue
            existing = rows.get(canonical.key)
            if existing is None:
                rows[canonical.key] = {
                    **_base_row(skill, canonical),
                    "importance": importance,
                    "weight":     skill.proficiency,
                }
            elif existing["importance"] == importance and skill.proficiency > existing["weight"]:
                existing["weight"] = skill.proficiency
                existing["level"]  = skill.level
    return list(rows.values()), unmapped


def owned_skill_rows(skills, ontology: Ontology | None = None) -> tuple[list[dict], list[str]]:
    """skills: anything with name / proficiency / level attributes
    (ExtractedSkill from a resume, SkillInput from a manual update)."""
    ontology = ontology or get_ontology()
    rows: dict[str, dict] = {}
    unmapped: list[str] = []
    for skill in skills:
        name = skill.name.strip()
        if not name:
            continue
        canonical = ontology.resolve(name)
        if canonical is None:
            _collect_unmapped(unmapped, name)
            continue
        existing = rows.get(canonical.key)
        if existing is None or skill.proficiency > existing["proficiency"]:
            rows[canonical.key] = {**_base_row(skill, canonical), "proficiency": skill.proficiency}
    return list(rows.values()), unmapped


def resolve_domains(domains: list[str], ontology: Ontology | None = None
                    ) -> tuple[list[str], list[str]]:
    """Returns (canonical domain keys, unknown inputs). Unknown domains are dropped."""
    ontology = ontology or get_ontology()
    keys: list[str] = []
    unknown: list[str] = []
    for d in domains:
        key = ontology.resolve_domain(d)
        if key is None:
            if d and d.strip() and d.strip().lower() != "null":
                unknown.append(d.strip())
        elif key not in keys:
            keys.append(key)
    return keys, unknown
