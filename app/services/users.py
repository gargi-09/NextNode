import asyncio
import uuid

from app.graph import catalog, writer
from app.graph.normalize import owned_skill_rows
from app.graph.ontology import get_ontology
from app.models.schemas import SkillInput, UserCreate


async def create_user(request: UserCreate) -> dict:
    user_id = request.id or str(uuid.uuid4())
    target_domain = get_ontology().resolve_domain(request.target_domain)
    await asyncio.to_thread(writer.create_user, user_id, request.name, target_domain)
    return await asyncio.to_thread(catalog.get_user, user_id)


async def get_user(user_id: str) -> dict:
    return await asyncio.to_thread(catalog.get_user, user_id)


async def list_users(skip: int, limit: int) -> list[dict]:
    return await asyncio.to_thread(catalog.list_users, skip, limit)


async def replace_skills(user_id: str, skills: list[SkillInput]) -> dict:
    # hand-entered skills follow the same strict rules as LLM output
    rows, unmapped = owned_skill_rows(skills)
    await asyncio.to_thread(writer.replace_user_skills, user_id, rows, unmapped)
    return await asyncio.to_thread(catalog.get_user, user_id)


async def delete_user(user_id: str) -> None:
    await asyncio.to_thread(writer.delete_user, user_id)
