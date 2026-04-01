from fastapi import APIRouter, HTTPException
from app.models.schemas import JDRequest, ResumeRequest
from app.extraction.extractor import extract_jd, extract_resume
from app.graph.writer import write_jd, write_resume
import uuid
from app.graph.reasoner import compute_gap
from app.extraction.explainer import explain_gap
from app.graph.reasoner import compute_gap
router = APIRouter()
    
@router.post("/extract/jd")
async def extract_job_description(request: JDRequest):
    try:
        job_id = request.job_id or str(uuid.uuid4())
        extracted = await extract_jd(request.text)
        write_jd(extracted, job_id)
        return {"job_id": job_id, "extracted": extracted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/extract/resume")
async def extract_resume_endpoint(request: ResumeRequest):
    try:
        extracted = await extract_resume(request.text)
        write_resume(extracted, request.user_id)
        return {"user_id": request.user_id, "extracted": extracted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/analyze/{user_id}/{job_id}")
async def analyze_gap(user_id: str, job_id: str, explain: bool = True):
    try:
        analysis = compute_gap(user_id, job_id)

        if explain:
            # fetch user and job names for the prompt
            from app.db.neo4j import neo4j_client
            with neo4j_client.get_session() as session:
                meta = session.run("""
                    MATCH (u:User {id: $user_id})
                    MATCH (j:JobPosting {id: $job_id})
                    RETURN u.name AS name, j.title AS title, j.company AS company
                """, user_id=user_id, job_id=job_id).single()

            explanation = await explain_gap(
                analysis=analysis,
                user_name=meta["name"],
                job_title=meta["title"],
                company=meta["company"]
            )
            analysis["explanation"] = explanation

        return analysis
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))