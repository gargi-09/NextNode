"""Graph writes. Every public function is ONE managed write transaction.

Rows arrive already resolved against the ontology (app.graph.normalize), so the
only Skill / Domain values written here are canonical ones — the LLM cannot
create graph nodes. Relationships are MERGEd on the node pair only (never on
properties), so re-ingestion updates edges instead of duplicating them.
"""
from app.db.neo4j import neo4j_client
from app.core.errors import ConflictError, NotFoundError

# Canonical skill upsert. Every value comes from the ontology file, so SET keeps the
# graph in sync with it; this also works before cypher/seed.cypher has been run.
_UPSERT_SKILL = """
    MERGE (s:Skill {name: row.name})
    SET s.key      = row.key,
        s.category = row.category,
        s.level    = row.base_level
"""


def _exists(tx, label: str, node_id: str) -> bool:
    # label is always one of our own constants, never user input
    return tx.run(
        f"OPTIONAL MATCH (n:{label} {{id: $id}}) RETURN n IS NOT NULL AS found",
        id=node_id
    ).single()["found"]


# ─────────────────────────── jobs ───────────────────────────

def write_job(job_id: str, meta: dict, rows: list[dict], domains: list[str],
              unmapped: list[str], source_hash: str | None = None,
              create_only: bool = False) -> bool:
    """Upsert a job and replace its requirements. Returns True if it was created.
    create_only=True raises ConflictError instead of overwriting."""
    return neo4j_client.write(
        _write_job_tx, job_id, meta, rows, domains, unmapped, source_hash, create_only
    )


def _write_job_tx(tx, job_id: str, meta: dict, rows: list[dict], domains: list[str],
                  unmapped: list[str], source_hash: str | None, create_only: bool) -> bool:
    existed = _exists(tx, "JobPosting", job_id)
    if create_only and existed:
        raise ConflictError(f"Job '{job_id}' already exists")

    tx.run("""
        MERGE (j:JobPosting {id: $job_id})
        SET j.title           = $title,
            j.company         = $company,
            j.seniority       = $seniority,
            j.source_hash     = $source_hash,
            j.unmapped_skills = $unmapped,
            j.updated_at      = datetime()
    """, job_id=job_id, title=meta["title"], company=meta.get("company"),
         seniority=meta.get("seniority"), source_hash=source_hash, unmapped=unmapped)

    # a posting's requirements come entirely from its latest version
    tx.run("""
        MATCH (:JobPosting {id: $job_id})-[r:REQUIRES_SKILL|IN_DOMAIN]->()
        DELETE r
    """, job_id=job_id)

    tx.run("""
        MATCH (j:JobPosting {id: $job_id})
        UNWIND $rows AS row
    """ + _UPSERT_SKILL + """
        MERGE (j)-[r:REQUIRES_SKILL]->(s)
        SET r.importance = row.importance,
            r.weight     = row.weight,
            r.level      = row.level
    """, job_id=job_id, rows=rows)

    tx.run("""
        MATCH (j:JobPosting {id: $job_id})
        UNWIND $domains AS name
        MERGE (d:Domain {name: name})
        MERGE (j)-[:IN_DOMAIN]->(d)
    """, job_id=job_id, domains=domains)

    return not existed


def delete_job(job_id: str):
    neo4j_client.write(_delete_tx, "JobPosting", job_id, "Job")


# ─────────────────────────── users ───────────────────────────

def create_user(user_id: str, name: str, target_domain: str | None):
    neo4j_client.write(_create_user_tx, user_id, name, target_domain)


def _create_user_tx(tx, user_id: str, name: str, target_domain: str | None):
    if _exists(tx, "User", user_id):
        raise ConflictError(f"User '{user_id}' already exists")

    tx.run("""
        CREATE (u:User {id: $user_id, name: $name, target_domain: $target_domain,
                        unmapped_skills: [], updated_at: datetime()})
    """, user_id=user_id, name=name, target_domain=target_domain)

    if target_domain:
        tx.run("""
            MATCH (u:User {id: $user_id})
            MERGE (d:Domain {name: $name})
            MERGE (u)-[:TARGETS]->(d)
        """, user_id=user_id, name=target_domain)


def replace_user_skills(user_id: str, rows: list[dict], unmapped: list[str]):
    """Set the user's complete skill list by hand (source "manual").
    Removes every existing HAS_SKILL edge, including resume-derived ones."""
    neo4j_client.write(_replace_user_skills_tx, user_id, rows, unmapped)


def _replace_user_skills_tx(tx, user_id: str, rows: list[dict], unmapped: list[str]):
    if not _exists(tx, "User", user_id):
        raise NotFoundError(f"User '{user_id}' not found")

    tx.run("""
        MATCH (u:User {id: $user_id})
        SET u.unmapped_skills = $unmapped,
            u.updated_at      = datetime()
        WITH u
        OPTIONAL MATCH (u)-[r:HAS_SKILL]->()
        DELETE r
    """, user_id=user_id, unmapped=unmapped)

    tx.run("""
        MATCH (u:User {id: $user_id})
        UNWIND $rows AS row
    """ + _UPSERT_SKILL + """
        MERGE (u)-[r:HAS_SKILL]->(s)
        SET r.proficiency = row.proficiency,
            r.level       = row.level,
            r.source      = "manual"
    """, user_id=user_id, rows=rows)


def write_resume(user_id: str, name: str, target_domain: str | None, rows: list[dict],
                 unmapped: list[str], source_hash: str) -> bool:
    """Replace resume-derived skills; skills set by hand are kept.
    Creates the user if needed. Returns True if the user was created."""
    return neo4j_client.write(
        _write_resume_tx, user_id, name, target_domain, rows, unmapped, source_hash
    )


def _write_resume_tx(tx, user_id: str, name: str, target_domain: str | None,
                     rows: list[dict], unmapped: list[str], source_hash: str) -> bool:
    existed = _exists(tx, "User", user_id)

    # a name or domain set by hand is kept
    tx.run("""
        MERGE (u:User {id: $user_id})
        SET u.name            = coalesce(u.name, $name),
            u.target_domain   = coalesce($target_domain, u.target_domain),
            u.source_hash     = $source_hash,
            u.unmapped_skills = $unmapped,
            u.updated_at      = datetime()
    """, user_id=user_id, name=name, target_domain=target_domain,
         source_hash=source_hash, unmapped=unmapped)

    # drop stale resume skills; manual ones stay
    tx.run("""
        MATCH (:User {id: $user_id})-[r:HAS_SKILL {source: "resume"}]->()
        DELETE r
    """, user_id=user_id)

    tx.run("""
        MATCH (u:User {id: $user_id})
        UNWIND $rows AS row
    """ + _UPSERT_SKILL + """
        MERGE (u)-[r:HAS_SKILL]->(s)
          ON CREATE SET r.source = "resume"
        WITH r, row
        WHERE r.source = "resume"
        SET r.proficiency = row.proficiency,
            r.level       = row.level
    """, user_id=user_id, rows=rows)

    if target_domain:
        tx.run("""
            MATCH (u:User {id: $user_id})
            OPTIONAL MATCH (u)-[old:TARGETS]->()
            DELETE old
            WITH DISTINCT u
            MERGE (d:Domain {name: $name})
            MERGE (u)-[:TARGETS]->(d)
        """, name=target_domain, user_id=user_id)

    return not existed


def delete_user(user_id: str):
    neo4j_client.write(_delete_tx, "User", user_id, "User")


# ─────────────────────────── shared ───────────────────────────

def _delete_tx(tx, label: str, node_id: str, kind: str):
    # removes the node and its edges; shared Skill / Domain nodes stay
    found = tx.run(f"""
        OPTIONAL MATCH (n:{label} {{id: $id}})
        WITH n, n IS NOT NULL AS found
        DETACH DELETE n
        RETURN found
    """, id=node_id).single()["found"]
    if not found:
        raise NotFoundError(f"{kind} '{node_id}' not found")
