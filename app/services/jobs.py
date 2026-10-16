import asyncio
import uuid

from app.graph import catalog, writer
from app.graph.normalize import required_skill_rows, resolve_domains
from app.models.schemas import ExtractedJD, ExtractedSkill, JobCreate, RequiredSkillInput
from app.services.ingestion import ingest_job_description


def _to_extracted(skills: list[RequiredSkillInput]) -> list[ExtractedSkill]:
    return [
        ExtractedSkill(name=s.name, category=s.category, level=s.level, proficiency=s.weight)
        for s in skills
    ]


async def _write_structured_job(request: JobCreate, create_only: bool) -> dict:
    job_id = request.id or str(uuid.uuid4())
    extracted = ExtractedJD(
        title=request.title,
        core_skills=_to_extracted(request.core_skills),
        preferred_skills=_to_extracted(request.preferred_skills),
        nice_to_have_skills=_to_extracted(request.nice_to_have_skills),
    )
    rows, unmapped = required_skill_rows(extracted)
    domains, unmapped_domains = resolve_domains(request.domains)
    meta = {"title": request.title, "company": request.company, "seniority": request.seniority}

    created = await asyncio.to_thread(
        writer.write_job, job_id, meta, rows, domains, unmapped, None, create_only)
    return {"job_id": job_id, "created": created, "skills_written": [r["name"] for r in rows],
            "unmapped_skills": unmapped, "unmapped_domains": unmapped_domains}


async def create_job(request: JobCreate) -> dict:
    """Structured job — no LLM call. Unknown skills land in unmapped_skills."""
    result = await _write_structured_job(request, create_only=True)
    return await asyncio.to_thread(catalog.get_job, result["job_id"])


async def upsert_job(request: JobCreate) -> dict:
    """Create or fully replace a structured job (used by the MCP server)."""
    return await _write_structured_job(request, create_only=False)


async def create_job_from_description(text: str, job_id: str | None = None) -> dict:
    return await ingest_job_description(text, job_id, create_only=True)


async def get_job(job_id: str) -> dict:
    return await asyncio.to_thread(catalog.get_job, job_id)


async def list_jobs(skip: int, limit: int) -> list[dict]:
    return await asyncio.to_thread(catalog.list_jobs, skip, limit)


async def delete_job(job_id: str) -> None:
    await asyncio.to_thread(writer.delete_job, job_id)
