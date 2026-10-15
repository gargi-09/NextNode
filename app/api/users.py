from fastapi import APIRouter, Query, Response
from app.api.errors import handle
from app.models.schemas import ResumeUpload, UserCreate, UserSkillsUpdate
from app.services import users
from app.services.ingestion import ingest_resume

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", status_code=201)
async def create_user(request: UserCreate):
    """Create a user without a resume. Returns 409 if the id is taken."""
    return await handle(users.create_user(request), "User creation")

@router.get("")
async def list_users(skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    return await handle(users.list_users(skip, limit), "User listing")

@router.get("/{user_id}")
async def get_user(user_id: str):
    return await handle(users.get_user(user_id), "User lookup")

@router.put("/{user_id}/skills")
async def replace_skills(user_id: str, request: UserSkillsUpdate):
    """Replace the user's entire skill list by hand (no LLM)."""
    return await handle(users.replace_skills(user_id, request.skills), "Skill update")

@router.post("/{user_id}/resume")
async def upload_resume(user_id: str, request: ResumeUpload):
    """Parse a resume with Gemini and update resume-derived skills.
    Creates the user if needed; skills set by hand are kept."""
    return await handle(ingest_resume(request.text, user_id), "Resume extraction")

@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: str):
    await handle(users.delete_user(user_id), "User deletion")
    return Response(status_code=204)
