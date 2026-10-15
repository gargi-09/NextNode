import asyncio

from app.extraction.extractor import extract_pivot_role
from app.graph.ontology import get_ontology
from app.graph.pivot import fetch_pivot_inputs
from app.graph.scoring import score_pivot


def _canonicalize_role(extracted) -> list[str]:
    """Rename role skills to their canonical spelling so they match the user's
    canonical skills. Unknown skills keep their name (they count as missing)."""
    ontology = get_ontology()
    unmapped: list[str] = []
    for skills in (extracted.core_skills, extracted.preferred_skills):
        for skill in skills:
            canonical = ontology.resolve(skill.name)
            if canonical:
                skill.name = canonical.name
            elif skill.name not in unmapped:
                unmapped.append(skill.name)
    return unmapped


async def analyze_pivot(user_id: str, target_role: str, job_id: str | None = None) -> dict:
    # read the graph first so an unknown user/job fails before spending an LLM call
    user_skills, jd_skills = await asyncio.to_thread(fetch_pivot_inputs, user_id, job_id)
    extracted, source = await extract_pivot_role(target_role)
    extracted = extracted.model_copy(deep=True)  # never mutate the cached copy
    unmapped = _canonicalize_role(extracted)
    result = score_pivot(user_id, extracted, user_skills, jd_skills)
    result["extraction_source"] = source
    result["unmapped_role_skills"] = unmapped
    return result
