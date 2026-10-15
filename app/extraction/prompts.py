# Bump whenever a prompt changes: it is part of the LLM cache key.
PROMPT_VERSION = "2"

JD_EXTRACTION_PROMPT = """
You are an expert technical recruiter. Extract structured information from the job description below.

Return ONLY valid JSON matching this exact structure — no markdown, no explanation:
{{
  "title": "job title",
  "company": "company name",
  "seniority": "junior|mid|senior|staff",
  "core_skills": [
    {{"name": "skill name", "category": "language|ml_framework|platform|concept|tool", "level": "foundational|intermediate|advanced", "proficiency": 0.0}}
  ],
  "preferred_skills": [...],
  "nice_to_have_skills": [...],
  "domains": ["nlp", "ml_infrastructure", "computer_vision", "data_engineering"]
}}

Rules:
- core_skills: explicitly required, deal-breakers if missing
- preferred_skills: strongly preferred but not blocking
- nice_to_have_skills: mentioned as a bonus
- proficiency: 0.7–1.0 for senior-level expectations, 0.4–0.6 for mid, 0.2–0.4 for junior
- Use canonical skill names. When a skill matches one of these, use exactly this spelling: {skill_vocabulary}
- domains: only include from the allowed list above

Job description:
{text}
"""

RESUME_EXTRACTION_PROMPT = """
You are an expert technical recruiter. Extract structured information from the resume below.

Return ONLY valid JSON matching this exact structure — no markdown, no explanation:
{{
  "name": "candidate name",
  "skills": [
    {{"name": "skill name", "category": "language|ml_framework|platform|concept|tool", "level": "foundational|intermediate|advanced", "proficiency": 0.0}}
  ],
  "target_domain": "nlp|ml_infrastructure|computer_vision|data_engineering|null"
}}

Rules:
- proficiency: infer from context (years mentioned, project complexity, role level)
  - 0.8–1.0: used in production, led projects, 3+ years
  - 0.5–0.7: used in coursework or side projects, some experience
  - 0.2–0.4: mentioned briefly, "familiar with", beginner
- Use canonical skill names. When a skill matches one of these, use exactly this spelling: {skill_vocabulary}
- target_domain: infer from their work history and projects, or null if unclear

Resume:
{text}
"""

PIVOT_ROLE_PROMPT = """
You are an expert technical recruiter with deep knowledge of ML and AI career paths.

Given the target role below, extract the typical skill requirements.

Return ONLY valid JSON — no markdown, no explanation:
{{
  "role_title": "canonical role title",
  "core_skills": [
    {{"name": "skill name", "category": "language|ml_framework|platform|concept|tool", "level": "foundational|intermediate|advanced", "proficiency": 0.0, "transferability": 0.0}}
  ],
  "preferred_skills": [...],
  "domains": ["nlp", "ml_infrastructure", "computer_vision", "data_engineering"]
}}

Rules:
- proficiency: typical expectation for this role (0.7–1.0 senior, 0.4–0.6 mid)
- transferability: how well this skill transfers FROM adjacent ML/AI roles (0.0–1.0)
  - 1.0: universal (Python, problem-solving, math)
  - 0.8: broadly transferable (PyTorch, ML fundamentals)
  - 0.5: somewhat transferable (domain-specific frameworks)
  - 0.2: role-specific (very niche tools)
- Use canonical skill names. When a skill matches one of these, use exactly this spelling: {skill_vocabulary}
- domains: only from the allowed list

Target role: {role}
"""