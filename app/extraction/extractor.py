"""Parse unstructured text into typed extractions — cache first, then Gemini.

Each extract_* returns (extraction, source) where source is "cache" or "llm".
Identical text (ignoring whitespace) never costs a second Gemini request while
it is cached in this process.
"""
from functools import lru_cache

from app.core.config import settings
from app.extraction.llm import generate_model
from app.extraction.prompts import (
    JD_EXTRACTION_PROMPT, PIVOT_ROLE_PROMPT, PROMPT_VERSION, RESUME_EXTRACTION_PROMPT
)
from app.extraction.quota import LRUCache, content_hash
from app.graph.ontology import get_ontology
from app.models.schemas import ExtractedJD, ExtractedPivotRole, ExtractedResume


@lru_cache(maxsize=1)
def get_cache() -> LRUCache:
    return LRUCache(settings.llm_cache_max_entries)


def extraction_hash(kind: str, text: str) -> str:
    return content_hash(kind, text, PROMPT_VERSION, settings.gemini_model)


def _vocabulary() -> str:
    return ", ".join(get_ontology().skill_names)


async def _cached_extract(kind: str, text: str, prompt: str, model_cls):
    key = extraction_hash(kind, text)
    cache = get_cache()
    hit = cache.get(key)
    if hit is not None:
        return model_cls(**hit), "cache"
    extracted = await generate_model(prompt, model_cls, kind="parse")
    cache.set(key, extracted.model_dump())
    return extracted, "llm"


async def extract_jd(text: str) -> tuple[ExtractedJD, str]:
    prompt = JD_EXTRACTION_PROMPT.format(text=text, skill_vocabulary=_vocabulary())
    return await _cached_extract("jd", text, prompt, ExtractedJD)


async def extract_resume(text: str) -> tuple[ExtractedResume, str]:
    prompt = RESUME_EXTRACTION_PROMPT.format(text=text, skill_vocabulary=_vocabulary())
    return await _cached_extract("resume", text, prompt, ExtractedResume)


async def extract_pivot_role(role: str) -> tuple[ExtractedPivotRole, str]:
    prompt = PIVOT_ROLE_PROMPT.format(role=role, skill_vocabulary=_vocabulary())
    return await _cached_extract("pivot_role", role, prompt, ExtractedPivotRole)
