import asyncio
import google.generativeai as genai
from app.core.config import settings
from app.extraction.extractor import _semaphore

genai.configure(api_key=settings.gemini_api_key)
model = genai.GenerativeModel("gemini-2.5-flash")

EXPLANATION_PROMPT = """
You are a candid, encouraging career coach for ML engineers. 
Given the analysis below, write a concise, personalized career gap report.

Analysis data:
- Candidate: {name}
- Job: {job_title} at {company}
- Fit score: {fit_score}%
- Domain bonus applied: {domain_bonus}

Skill breakdown:
{skill_summary}

Roadmap:
{roadmap_summary}

Write your response in this exact structure:

## Overall fit
2-3 sentences. Be specific about the fit score and what's driving it. 
Mention the domain alignment bonus if it applied.

## Your strengths for this role
Bullet points. Only mention skills with status "met". Be specific.

## Gaps to close
For each gap in the roadmap, one bullet. Include priority and the prereq 
context if prereqs_owned > 0 — tell them why it's achievable.

## Recommended next steps
3 concrete, actionable steps ordered by priority. Be specific — 
name actual courses, projects, or resources where relevant.

Keep the tone direct and honest. No fluff. Max 300 words.
"""

async def explain_gap(analysis: dict, user_name: str, job_title: str, company: str) -> str:
    skill_summary = "\n".join([
        f"- {s['skill']}: {s['status']} "
        f"(importance: {s['importance']}, "
        f"your proficiency: {s['your_proficiency']}, "
        f"prereqs owned: {s['prereqs_owned']})"
        for s in analysis["skill_breakdown"]
    ])

    roadmap_summary = "\n".join([
        f"- Rank {r['rank']}: {r['skill']} "
        f"[{r['priority']} priority] — {r['reason']}"
        for r in analysis["roadmap"]
    ])

    prompt = EXPLANATION_PROMPT.format(
        name=user_name,
        job_title=job_title,
        company=company if company else "the company",
        fit_score=round(analysis["fit_score"] * 100, 1),
        domain_bonus="yes" if analysis["domain_bonus"] > 0 else "no",
        skill_summary=skill_summary,
        roadmap_summary=roadmap_summary
    )

    async with _semaphore:
        response = await asyncio.to_thread(
            model.generate_content,
            prompt,
            generation_config={"temperature": 0.4}
        )
        return response.text.strip()