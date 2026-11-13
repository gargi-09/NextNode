"""The public analyze flow: what a hosted instance offers to strangers.

    resume text (+ job text or a sample job)
      -> Gemini parse (same limiter + content-hash cache) -> strict ontology resolution
      -> written under fresh random ids ("pub-...") -> deterministic gap analysis
      -> both nodes deleted before the response goes out

Scoring runs as Cypher in Neo4j, so a visitor's skills must exist in the graph for the length
of their request; they never outlive it. No raw resume text is stored: nodes carry only the
resolved skills and a content hash, and they are deleted. A sweep removes anything a crashed
request left behind (writer.sweep_ephemeral).
"""
import asyncio
import logging
import uuid

from app.core.errors import NotFoundError, UnprocessableError
from app.graph import writer
from app.models.schemas import JobCreate, RequiredSkillInput
from app.services import jobs
from app.services.analysis import analyze_gap
from app.services.ingestion import ingest_job_description, ingest_resume

logger = logging.getLogger(__name__)

PREFIX = "pub-"


def _skills(pairs) -> list[RequiredSkillInput]:
    return [RequiredSkillInput(name=n, weight=w) for n, w in pairs]


# The three jobs of the Pages demo (scripts/build_pages.py SCENARIOS; a test keeps them equal),
# so a visitor without a job description can still try it.
SAMPLE_JOBS = {
    "llm-platform": JobCreate(
        title="ML Engineer, LLM Platform", company="Acme AI", domains=["nlp", "ml_infrastructure"],
        core_skills=_skills([("Python", 0.7), ("PyTorch", 0.7), ("LLM fine-tuning", 0.6),
                             ("HuggingFace Transformers", 0.6)]),
        preferred_skills=_skills([("Vector Databases", 0.5), ("RAG", 0.5)]),
        nice_to_have_skills=_skills([("Docker", 0.4), ("k8s", 0.3)])),
    "data-eng": JobCreate(
        title="Data Engineer", company="Northwind", domains=["data_engineering"],
        core_skills=_skills([("SQL", 0.7), ("Apache Spark", 0.6), ("Airflow", 0.6)]),
        preferred_skills=_skills([("Snowflake", 0.5), ("Kafka", 0.5)]),
        nice_to_have_skills=_skills([("Databricks", 0.4), ("Docker", 0.3)])),
    "cv-pivot": JobCreate(
        title="Computer Vision Engineer", company="Visionary Labs", domains=["computer_vision"],
        core_skills=_skills([("PyTorch", 0.7), ("Computer Vision", 0.7), ("Deep Learning", 0.6)]),
        preferred_skills=_skills([("OpenCV", 0.6), ("Docker", 0.4)]),
        nice_to_have_skills=_skills([("Kubernetes", 0.3)])),
}


def sample_jobs() -> list[dict]:
    return [{"id": k, "title": j.title, "company": j.company,
             "skills": [s.name for s in j.core_skills + j.preferred_skills + j.nice_to_have_skills]}
            for k, j in SAMPLE_JOBS.items()]


async def analyze_public(resume_text: str, job_text: str | None = None, sample_job: str | None = None,
                         explain: bool = False) -> dict:
    if (job_text is None) == (sample_job is None):
        raise UnprocessableError("Send exactly one of job_text or sample_job")
    if sample_job is not None and sample_job not in SAMPLE_JOBS:
        raise UnprocessableError(f"Unknown sample_job '{sample_job}'; see GET /api/v1/public/sample-jobs")

    session = PREFIX + uuid.uuid4().hex[:16]
    user_id, job_id = f"{session}-user", f"{session}-job"
    job_step = (jobs.create_job(SAMPLE_JOBS[sample_job].model_copy(update={"id": job_id}))
                if sample_job is not None else ingest_job_description(job_text, job_id, create_only=True))
    try:
        # the two parses are independent: run them together (half the wait on two Gemini calls),
        # and let both finish before cleanup so neither writes after its node was deleted
        resume, job = await asyncio.gather(ingest_resume(resume_text, user_id), job_step,
                                           return_exceptions=True)
        for result in (resume, job):
            if isinstance(result, BaseException):
                raise result
        job_unmapped = job.get("unmapped_skills") or []
        analysis = await analyze_gap(user_id, job_id, explain)
    finally:
        await _forget(user_id, job_id)
    return {"analysis": analysis,
            "unmapped_skills": {"resume": resume["unmapped_skills"] or [], "job": job_unmapped},
            "stored": False}


async def _forget(user_id: str, job_id: str) -> None:
    for delete, node_id in ((writer.delete_user, user_id), (writer.delete_job, job_id)):
        try:
            await asyncio.to_thread(delete, node_id)
        except NotFoundError:
            pass                       # never written (the request failed before that step)
        except Exception:              # graph down: the sweep removes it later
            logger.warning("Could not delete visitor node %s; the sweep will remove it", node_id)


async def sweep(older_than_s: int) -> int:
    removed = await asyncio.to_thread(writer.sweep_ephemeral, PREFIX, older_than_s)
    if removed:
        logger.info("Swept %d leftover visitor node(s)", removed)
    return removed
