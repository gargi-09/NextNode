from pydantic import BaseModel
from typing import Optional

class ExtractedSkill(BaseModel):
    name: str
    category: str        # language | ml_framework | platform | concept | tool
    level: str           # foundational | intermediate | advanced
    proficiency: float   # 0.0–1.0, inferred from context

class ExtractedJD(BaseModel):
    title: str
    company: str
    seniority: str       # junior | mid | senior | staff
    core_skills: list[ExtractedSkill]
    preferred_skills: list[ExtractedSkill]
    nice_to_have_skills: list[ExtractedSkill]
    domains: list[str]   # e.g. ["nlp", "ml_infrastructure"]

class ExtractedResume(BaseModel):
    name: str
    skills: list[ExtractedSkill]
    target_domain: Optional[str] = None

class JDRequest(BaseModel):
    text: str
    job_id: Optional[str] = None  # optional — auto-generated if missing

class ResumeRequest(BaseModel):
    text: str
    user_id: str