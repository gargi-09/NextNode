from app.db.neo4j import neo4j_client
from app.core.errors import NotFoundError
from app.graph.scoring import score_gap


def fetch_gap_inputs(user_id: str, job_id: str) -> dict:
    """Blocking graph reads for a user/job gap analysis. Raises NotFoundError."""
    return neo4j_client.read(_fetch_gap_inputs_tx, user_id, job_id)


def compute_gap(user_id: str, job_id: str) -> dict:
    inputs = fetch_gap_inputs(user_id, job_id)
    return score_gap(
        user_id, job_id,
        inputs["rows"], inputs["shared_domains"], inputs["prereq_coverage"]
    )


def _fetch_gap_inputs_tx(tx, user_id: str, job_id: str) -> dict:

    # ── Step 0: both endpoints must exist (a missing job used to score 100%) ──
    meta = tx.run("""
        OPTIONAL MATCH (u:User {id: $user_id})
        OPTIONAL MATCH (j:JobPosting {id: $job_id})
        RETURN u IS NOT NULL AS user_exists,
               j IS NOT NULL AS job_exists,
               u.name AS name, j.title AS title, j.company AS company
    """, user_id=user_id, job_id=job_id).single()

    if not meta["user_exists"]:
        raise NotFoundError(f"User '{user_id}' not found")
    if not meta["job_exists"]:
        raise NotFoundError(f"Job '{job_id}' not found")

    # ── Step 1: raw gap query (pure Cypher symbolic reasoning) ──
    result = tx.run("""
        MATCH (j:JobPosting {id: $job_id})-[req:REQUIRES_SKILL]->(s:Skill)
        OPTIONAL MATCH (u:User {id: $user_id})-[has:HAS_SKILL]->(s)
        RETURN
            s.name            AS skill,
            s.category        AS category,
            req.importance    AS importance,
            req.weight        AS required_weight,
            COALESCE(has.proficiency, 0.0) AS your_proficiency,
            CASE
                WHEN has.proficiency IS NULL THEN 'missing'
                WHEN has.proficiency >= req.weight THEN 'met'
                ELSE 'partial'
            END AS status
    """, user_id=user_id, job_id=job_id)

    rows = [dict(r) for r in result]

    # ── Step 2: domain alignment — only domains BOTH sides point at ──
    shared_domains = tx.run("""
        MATCH (u:User {id: $user_id})-[:TARGETS]->(d:Domain)
              <-[:IN_DOMAIN]-(j:JobPosting {id: $job_id})
        RETURN count(DISTINCT d) AS shared_domains
    """, user_id=user_id, job_id=job_id).single()["shared_domains"]

    # ── Step 3: prerequisite coverage for missing skills ──
    # DISTINCT: several prerequisite paths through the same owned skill count once
    prereq_result = tx.run("""
        MATCH (j:JobPosting {id: $job_id})-[:REQUIRES_SKILL]->(target:Skill)
        WHERE NOT EXISTS {
            MATCH (:User {id: $user_id})-[:HAS_SKILL]->(target)
        }
        OPTIONAL MATCH (:User {id: $user_id})-[:HAS_SKILL]->(owned:Skill)
            -[:PREREQUISITE_OF*1..3]->(target)
        RETURN
            target.name AS skill,
            count(DISTINCT owned) AS prereqs_owned
    """, user_id=user_id, job_id=job_id)

    prereq_coverage = {
        r["skill"]: r["prereqs_owned"]
        for r in prereq_result
    }

    # ── Step 4: graph state for the frontend (actual edges, not derived) ──
    # paths from skills the user owns to required skills they are missing
    path_result = tx.run("""
        MATCH (j:JobPosting {id: $job_id})-[:REQUIRES_SKILL]->(target:Skill)
        WHERE NOT EXISTS {
            MATCH (:User {id: $user_id})-[:HAS_SKILL]->(target)
        }
        MATCH p = (:User {id: $user_id})-[has:HAS_SKILL]->(owned:Skill)
                  -[:PREREQUISITE_OF*1..3]->(target)
        RETURN owned.name AS owned, has.proficiency AS proficiency, target.name AS target,
               [r IN relationships(p)[1..] | {
                   source: startNode(r).name, target: endNode(r).name, strength: r.strength
               }] AS edges
    """, user_id=user_id, job_id=job_id)
    prereq_paths = [dict(r) for r in path_result]

    # prerequisite edges between two skills the job itself requires
    required_edges = tx.run("""
        MATCH (j:JobPosting {id: $job_id})-[:REQUIRES_SKILL]->(a:Skill)
              -[r:PREREQUISITE_OF]->(b:Skill)<-[:REQUIRES_SKILL]-(j)
        RETURN a.name AS source, b.name AS target, r.strength AS strength
    """, job_id=job_id)
    required_prereq_edges = [dict(r) for r in required_edges]

    domain_record = tx.run("""
        OPTIONAL MATCH (:User {id: $user_id})-[:TARGETS]->(ud:Domain)
        WITH collect(DISTINCT ud.name) AS user_domains
        OPTIONAL MATCH (:JobPosting {id: $job_id})-[:IN_DOMAIN]->(jd:Domain)
        RETURN user_domains, collect(DISTINCT jd.name) AS job_domains
    """, user_id=user_id, job_id=job_id).single()

    return {
        "rows":            rows,
        "shared_domains":  shared_domains,
        "prereq_coverage": prereq_coverage,
        "user_name":       meta["name"],
        "job_title":       meta["title"],
        "company":         meta["company"],
        "prereq_paths":    prereq_paths,
        "required_prereq_edges": required_prereq_edges,
        "user_domains":    domain_record["user_domains"] if domain_record else [],
        "job_domains":     domain_record["job_domains"] if domain_record else [],
    }
