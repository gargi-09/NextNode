from fastapi import APIRouter
from app.api.errors import handle as _handle
from app.models.analysis import AnalysisResponse
from app.models.schemas import JDRequest, PivotRequest, ResumeRequest
from app.services.ingestion import ingest_job_description, ingest_resume
from app.services.analysis import analyze_gap as analyze_gap_service
from app.services.pivot import analyze_pivot as analyze_pivot_service

router = APIRouter()


@router.post("/extract/jd")
async def extract_job_description(request: JDRequest):
    return await _handle(
        ingest_job_description(request.text, request.job_id),
        "Job description extraction"
    )

@router.post("/extract/resume")
async def extract_resume_endpoint(request: ResumeRequest):
    return await _handle(
        ingest_resume(request.text, request.user_id),
        "Resume extraction"
    )

@router.get("/analyze/{user_id}/{job_id}", response_model=AnalysisResponse)
async def analyze_gap(user_id: str, job_id: str, explain: bool = True):
    """Deterministic gap analysis plus graph payloads for react-force-graph
    (`graph`) and @xyflow/react (`roadmap_flow`)."""
    return await _handle(
        analyze_gap_service(user_id, job_id, explain),
        "Gap analysis"
    )

@router.post("/pivot")
async def analyze_pivot(request: PivotRequest):
    return await _handle(
        analyze_pivot_service(request.user_id, request.target_role, request.job_id),
        "Pivot analysis"
    )
