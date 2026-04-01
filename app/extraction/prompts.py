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
- Use canonical skill names (e.g. "PyTorch" not "pytorch", "LangChain" not "langchain")
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
- Use canonical skill names (e.g. "PyTorch" not "pytorch")
- target_domain: infer from their work history and projects, or null if unclear

Resume:
{text}
"""