from fastapi import APIRouter, Query, Response
from app.api.errors import handle
from app.models.schemas import JobCreate, JobFromDescription
from app.services import jobs

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", status_code=201)
async def create_job(request: JobCreate):
    """Create a job from structured fields (no LLM). Returns 409 if the id is taken."""
    return await handle(jobs.create_job(request), "Job creation")

@router.post("/from-description", status_code=201)
async def create_job_from_description(request: JobFromDescription):
    """Create a job by parsing description text with Gemini."""
    return await handle(
        jobs.create_job_from_description(request.text, request.id),
        "Job extraction"
    )

@router.get("")
async def list_jobs(skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    return await handle(jobs.list_jobs(skip, limit), "Job listing")

@router.get("/{job_id}")
async def get_job(job_id: str):
    return await handle(jobs.get_job(job_id), "Job lookup")

@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: str):
    await handle(jobs.delete_job(job_id), "Job deletion")
    return Response(status_code=204)
