import asyncio
import json
import google.generativeai as genai
from app.core.config import settings
from app.models.schemas import ExtractedJD, ExtractedResume
from app.extraction.prompts import JD_EXTRACTION_PROMPT, RESUME_EXTRACTION_PROMPT

genai.configure(api_key=settings.gemini_api_key)
model = genai.GenerativeModel("gemini-2.5-flash")  # or "gemini-2.5-pro" for better quality but higher latency

# Global semaphore — max 8 concurrent Gemini calls (safely under 10 RPM)
_semaphore = asyncio.Semaphore(8)

async def _call_gemini(prompt: str) -> dict:
    async with _semaphore:
        response = await asyncio.to_thread(
            model.generate_content,
            prompt,
            generation_config={"temperature": 0.1}  # low temp = consistent structured output
        )
        raw = response.text.strip()
        # strip markdown fences if Gemini adds them anyway
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(raw)

async def extract_jd(text: str) -> ExtractedJD:
    prompt = JD_EXTRACTION_PROMPT.format(text=text)
    data = await _call_gemini(prompt)
    return ExtractedJD(**data)

async def extract_resume(text: str) -> ExtractedResume:
    prompt = RESUME_EXTRACTION_PROMPT.format(text=text)
    data = await _call_gemini(prompt)
    return ExtractedResume(**data)