from pydantic import BaseModel, Field
from typing import Optional

class ExtractedSkill(BaseModel):
    name: str
    category: Optional[str] = None  # language | ml_framework | platform | concept | tool
    level: Optional[str] = None     # foundational | intermediate | advanced
    proficiency: float              # 0.0–1.0, inferred from context

class ExtractedJD(BaseModel):
    title: str
    company: Optional[str] = None
    seniority: Optional[str] = None  # junior | mid | senior | staff
    core_skills: list[ExtractedSkill]
    preferred_skills: list[ExtractedSkill]
    nice_to_have_skills: list[ExtractedSkill]
    domains: list[str] = []  # e.g. ["nlp", "ml_infrastructure"]

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

class PivotSkill(BaseModel):
    name: str
    category: str
    level: str
    proficiency: float
    transferability: float

class ExtractedPivotRole(BaseModel):
    role_title: str
    core_skills: list[PivotSkill]
    preferred_skills: list[PivotSkill]
    domains: list[str]

class PivotRequest(BaseModel):
    user_id: str
    target_role: str
    job_id: Optional[str] = None  # optional JD context

# ─── Direct user / job management (no LLM) ───

class SkillInput(BaseModel):
    name: str = Field(min_length=1)
    proficiency: float = Field(ge=0.0, le=1.0)
    category: Optional[str] = None
    level: Optional[str] = None

class RequiredSkillInput(BaseModel):
    name: str = Field(min_length=1)
    weight: float = Field(ge=0.0, le=1.0, description="proficiency the job expects")
    category: Optional[str] = None
    level: Optional[str] = None

class UserCreate(BaseModel):
    id: Optional[str] = None  # optional — auto-generated if missing
    name: str = Field(min_length=1)
    target_domain: Optional[str] = None

class UserSkillsUpdate(BaseModel):
    skills: list[SkillInput]

class ResumeUpload(BaseModel):
    text: str = Field(min_length=1)

class JobCreate(BaseModel):
    id: Optional[str] = None  # optional — auto-generated if missing
    title: str = Field(min_length=1)
    company: Optional[str] = None
    seniority: Optional[str] = None
    core_skills: list[RequiredSkillInput] = []
    preferred_skills: list[RequiredSkillInput] = []
    nice_to_have_skills: list[RequiredSkillInput] = []
    domains: list[str] = []

class JobFromDescription(BaseModel):
    id: Optional[str] = None
    text: str = Field(min_length=1)
