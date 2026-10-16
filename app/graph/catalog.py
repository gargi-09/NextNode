"""Read-only lookups for users, jobs and the ontology review queue."""
from app.db.neo4j import neo4j_client
from app.core.errors import NotFoundError
from app.graph.scoring import TIER_ORDER


def get_user(user_id: str) -> dict:
    return neo4j_client.read(_get_user_tx, user_id)


def _get_user_tx(tx, user_id: str) -> dict:
    record = tx.run("""
        MATCH (u:User {id: $user_id})
        OPTIONAL MATCH (u)-[r:HAS_SKILL]->(s:Skill)
        WITH u, collect(s {
            .name, .key, .category,
            proficiency: r.proficiency, level: r.level, source: r.source
        }) AS skills
        RETURN u.id AS id, u.name AS name, u.target_domain AS target_domain,
               coalesce(u.unmapped_skills, []) AS unmapped_skills, skills
    """, user_id=user_id).single()

    if record is None:
        raise NotFoundError(f"User '{user_id}' not found")

    user = dict(record)
    user["skills"] = sorted(user["skills"], key=lambda s: -(s["proficiency"] or 0))
    return user


def list_users(skip: int = 0, limit: int = 50) -> list[dict]:
    return neo4j_client.read(_list_users_tx, skip, limit)


def _list_users_tx(tx, skip: int, limit: int) -> list[dict]:
    result = tx.run("""
        MATCH (u:User)
        OPTIONAL MATCH (u)-[r:HAS_SKILL]->()
        WITH u, count(r) AS skill_count
        RETURN u.id AS id, u.name AS name, u.target_domain AS target_domain, skill_count,
               size(coalesce(u.unmapped_skills, [])) AS unmapped_count
        ORDER BY name, id
        SKIP $skip LIMIT $limit
    """, skip=skip, limit=limit)
    return [dict(r) for r in result]


def get_job(job_id: str) -> dict:
    return neo4j_client.read(_get_job_tx, job_id)


def _get_job_tx(tx, job_id: str) -> dict:
    record = tx.run("""
        MATCH (j:JobPosting {id: $job_id})
        OPTIONAL MATCH (j)-[r:REQUIRES_SKILL]->(s:Skill)
        WITH j, collect(s {
            .name, .key, .category,
            importance: r.importance, weight: r.weight, level: r.level
        }) AS skills
        OPTIONAL MATCH (j)-[:IN_DOMAIN]->(d:Domain)
        RETURN j.id AS id, j.title AS title, j.company AS company,
               j.seniority AS seniority, skills, collect(d.name) AS domains,
               coalesce(j.unmapped_skills, []) AS unmapped_skills
    """, job_id=job_id).single()

    if record is None:
        raise NotFoundError(f"Job '{job_id}' not found")

    job = dict(record)
    job["skills"] = sorted(job["skills"], key=lambda s: (
        TIER_ORDER.get(s["importance"], 3), -(s["weight"] or 0)
    ))
    return job


def list_jobs(skip: int = 0, limit: int = 50) -> list[dict]:
    return neo4j_client.read(_list_jobs_tx, skip, limit)


def _list_jobs_tx(tx, skip: int, limit: int) -> list[dict]:
    result = tx.run("""
        MATCH (j:JobPosting)
        OPTIONAL MATCH (j)-[r:REQUIRES_SKILL]->()
        WITH j, count(r) AS skill_count
        RETURN j.id AS id, j.title AS title, j.company AS company,
               j.seniority AS seniority, skill_count,
               size(coalesce(j.unmapped_skills, [])) AS unmapped_count
        ORDER BY title, id
        SKIP $skip LIMIT $limit
    """, skip=skip, limit=limit)
    return [dict(r) for r in result]


def job_exists(job_id: str) -> bool:
    return neo4j_client.read(_exists_tx, "JobPosting", job_id)["exists"]


def get_source_hash(label: str, node_id: str) -> dict:
    """{"exists": bool, "source_hash": str | None} — lets ingestion skip unchanged text."""
    return neo4j_client.read(_exists_tx, label, node_id)


def _exists_tx(tx, label: str, node_id: str) -> dict:
    # label is always one of our own constants, never user input
    record = tx.run(f"""
        OPTIONAL MATCH (n:{label} {{id: $id}})
        RETURN n IS NOT NULL AS exists, n.source_hash AS source_hash
    """, id=node_id).single()
    return {"exists": record["exists"], "source_hash": record["source_hash"]}


def unmapped_terms(limit: int = 100) -> list[dict]:
    """Ontology review queue: unknown terms across all users and jobs, most frequent first."""
    return neo4j_client.read(_unmapped_terms_tx, limit)


def _unmapped_terms_tx(tx, limit: int) -> list[dict]:
    result = tx.run("""
        MATCH (n) WHERE (n:User OR n:JobPosting) AND size(coalesce(n.unmapped_skills, [])) > 0
        UNWIND n.unmapped_skills AS term
        WITH toLower(trim(term)) AS term, count(*) AS freq,
             sum(CASE WHEN n:User THEN 1 ELSE 0 END) AS in_users,
             sum(CASE WHEN n:JobPosting THEN 1 ELSE 0 END) AS in_jobs
        RETURN term, freq, in_users, in_jobs
        ORDER BY freq DESC, term
        LIMIT $limit
    """, limit=limit)
    return [dict(r) for r in result]


def skill_prerequisites(skill_name: str, max_depth: int = 3) -> list[dict]:
    """Skills that lead to `skill_name` through PREREQUISITE_OF chains (1..3 hops).
    `strength` is the product of edge strengths along the strongest shortest path."""
    return neo4j_client.read(_skill_prerequisites_tx, skill_name, max_depth)


def _skill_prerequisites_tx(tx, skill_name: str, max_depth: int) -> list[dict]:
    # max_depth is an int we control (bounded below), never user input in the query text
    depth = max(1, min(int(max_depth), 3))
    result = tx.run(f"""
        MATCH p = (pre:Skill)-[:PREREQUISITE_OF*1..{depth}]->(:Skill {{name: $name}})
        WITH pre, length(p) AS depth,
             reduce(s = 1.0, r IN relationships(p) | s * coalesce(r.strength, 1.0)) AS strength
        ORDER BY depth, strength DESC
        WITH pre, collect({{depth: depth, strength: strength}})[0] AS best
        RETURN pre.name AS skill, best.depth AS depth, round(best.strength, 3) AS strength
        ORDER BY depth, strength DESC, skill
    """, name=skill_name)
    return [dict(r) for r in result]
