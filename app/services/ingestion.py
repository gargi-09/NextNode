"""Ingestion pipeline (TDD §5.3):

  text → content hash
       → unchanged?  (node already has this source_hash) → no LLM call, no write
       → cache hit?  → no LLM call
       → rate limiter → Gemini → cache            (QuotaExceeded → HTTP 429 + Retry-After)
       → ontology resolution (unknown terms → unmapped list, zero new nodes)
       → ONE write transaction                    (graph down → HTTP 503; extraction
                                                   stays cached, so a retry is free)
"""
import asyncio
import uuid

from app.core.errors import ConflictError
from app.extraction.extractor import extract_jd, extract_resume, extraction_hash
from app.graph import catalog, writer
from app.graph.normalize import owned_skill_rows, required_skill_rows, resolve_domains
from app.graph.ontology import get_ontology


async def ingest_resume(text: str, user_id: str) -> dict:
    source_hash = extraction_hash("resume", text)
    state = await asyncio.to_thread(catalog.get_source_hash, "User", user_id)
    if state["source_hash"] == source_hash:
        return {"user_id": user_id, "status": "unchanged", "extraction_source": "skipped",
                "skills_written": None, "unmapped_skills": None, "extracted": None}

    extracted, source = await extract_resume(text)
    rows, unmapped = owned_skill_rows(extracted.skills)
    target_domain = get_ontology().resolve_domain(extracted.target_domain)

    created = await asyncio.to_thread(
        writer.write_resume, user_id, extracted.name, target_domain, rows, unmapped, source_hash
    )
    return {
        "user_id": user_id,
        "status": "created" if created else "updated",
        "extraction_source": source,
        "skills_written": [r["name"] for r in rows],
        "unmapped_skills": unmapped,
        "extracted": extracted,
    }


async def ingest_job_description(text: str, job_id: str | None = None,
                                 create_only: bool = False) -> dict:
    job_id = job_id or str(uuid.uuid4())
    source_hash = extraction_hash("jd", text)

    # checked before the LLM call so a taken id or unchanged text costs no quota
    state = await asyncio.to_thread(catalog.get_source_hash, "JobPosting", job_id)
    if state["exists"] and create_only:
        raise ConflictError(f"Job '{job_id}' already exists")
    if state["source_hash"] == source_hash:
        return {"job_id": job_id, "status": "unchanged", "extraction_source": "skipped",
                "skills_written": None, "unmapped_skills": None, "unmapped_domains": None,
                "extracted": None}

    extracted, source = await extract_jd(text)
    rows, unmapped = required_skill_rows(extracted)
    domains, unknown_domains = resolve_domains(extracted.domains)
    meta = {"title": extracted.title, "company": extracted.company,
            "seniority": extracted.seniority}

    created = await asyncio.to_thread(
        writer.write_job, job_id, meta, rows, domains, unmapped, source_hash, create_only
    )
    return {
        "job_id": job_id,
        "status": "created" if created else "updated",
        "extraction_source": source,
        "skills_written": [r["name"] for r in rows],
        "unmapped_skills": unmapped,
        "unmapped_domains": unknown_domains,
        "extracted": extracted,
    }
