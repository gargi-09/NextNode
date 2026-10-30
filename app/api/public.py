from fastapi import APIRouter, HTTPException, Request

from app.api.errors import handle
from app.core.config import settings
from app.core.ratelimit import ClientRateLimiter
from app.models.schemas import PublicAnalyzeRequest
from app.services.public import analyze_public, sample_jobs

router = APIRouter(prefix="/public", tags=["public"])

limiter = ClientRateLimiter(settings.public_rate_per_minute, settings.public_rate_per_day)


def client_ip(request: Request) -> str:
    """Behind Cloud Run the socket peer is Google's front end; the client is the last
    X-Forwarded-For hop, which the front end appends (earlier hops are client-supplied)."""
    forwarded = request.headers.get("x-forwarded-for")
    if settings.public_trust_forwarded_for and forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


@router.get("/sample-jobs")
def list_sample_jobs():
    """Jobs a visitor can pick instead of pasting a description."""
    return sample_jobs()


@router.post("/analyze")
async def analyze(body: PublicAnalyzeRequest, request: Request):
    """Parse a resume (and a job description, or use a sample job), score the gap with the
    deterministic engine, and return the analysis. Nothing is kept after the response."""
    wait = limiter.acquire(client_ip(request))
    if wait is not None:
        raise HTTPException(status_code=429, headers={"Retry-After": str(wait)},
                            detail={"code": "RATE_LIMITED", "retry_after": wait,
                                    "message": "Too many analyses from this address. Retry later."})
    return await handle(analyze_public(body.resume_text, body.job_text, body.sample_job, body.explain),
                        "Public analysis")
