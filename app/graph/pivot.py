from app.db.neo4j import neo4j_client
from app.core.errors import NotFoundError


def fetch_pivot_inputs(user_id: str, job_id: str = None) -> tuple[dict, dict]:
    """Blocking graph reads for a pivot analysis.

    Returns (user_skills, jd_skills) as {skill name: proficiency / required weight}.
    Raises NotFoundError for an unknown user, or an unknown job when job_id is given.
    """
    return neo4j_client.read(_fetch_pivot_inputs_tx, user_id, job_id)


def _fetch_pivot_inputs_tx(tx, user_id: str, job_id: str = None) -> tuple[dict, dict]:
    exists = tx.run("""
        OPTIONAL MATCH (u:User {id: $user_id})
        OPTIONAL MATCH (j:JobPosting {id: $job_id})
        RETURN u IS NOT NULL AS user_exists, j IS NOT NULL AS job_exists
    """, user_id=user_id, job_id=job_id).single()

    if not exists["user_exists"]:
        raise NotFoundError(f"User '{user_id}' not found")
    if job_id and not exists["job_exists"]:
        raise NotFoundError(f"Job '{job_id}' not found")

    # get user skills
    result = tx.run("""
        MATCH (u:User {id: $user_id})-[h:HAS_SKILL]->(s:Skill)
        RETURN s.name AS skill, h.proficiency AS proficiency
    """, user_id=user_id)
    user_skills = {r["skill"]: r["proficiency"] for r in result}

    # if JD provided, get current role skills for overlap analysis
    jd_skills = {}
    if job_id:
        jd_result = tx.run("""
            MATCH (j:JobPosting {id: $job_id})-[req:REQUIRES_SKILL]->(s:Skill)
            RETURN s.name AS skill, req.importance AS importance, req.weight AS weight
        """, job_id=job_id)
        jd_skills = {r["skill"]: r["weight"] for r in jd_result}

    return user_skills, jd_skills
