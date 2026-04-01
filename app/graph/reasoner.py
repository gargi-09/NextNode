from app.db.neo4j import neo4j_client

def compute_gap(user_id: str, job_id: str) -> dict:
    with neo4j_client.get_session() as session:

        # ── Step 1: raw gap query (pure Cypher symbolic reasoning) ──
        result = session.run("""
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

        # ── Step 2: domain alignment bonus ──
        domain_result = session.run("""
            MATCH (u:User {id: $user_id})-[:TARGETS]->(ud:Domain)
            MATCH (j:JobPosting {id: $job_id})-[:IN_DOMAIN]->(jd:Domain)
            RETURN count(*) AS shared_domains
        """, user_id=user_id, job_id=job_id)

        domain_row = domain_result.single()
        shared_domains = domain_row["shared_domains"] if domain_row else 0

        # ── Step 3: prerequisite coverage for missing skills ──
        prereq_result = session.run("""
            MATCH (j:JobPosting {id: $job_id})-[:REQUIRES_SKILL]->(target:Skill)
            WHERE NOT EXISTS {
                MATCH (u:User {id: $user_id})-[:HAS_SKILL]->(target)
            }
            OPTIONAL MATCH (u:User {id: $user_id})-[:HAS_SKILL]->(owned:Skill)
                -[:PREREQUISITE_OF*1..3]->(target)
            RETURN
                target.name AS skill,
                count(owned) AS prereqs_owned
        """, user_id=user_id, job_id=job_id)

        prereq_coverage = {
            r["skill"]: r["prereqs_owned"]
            for r in prereq_result
        }

    # ── Step 4: symbolic scoring rules ──
    importance_weights = {
        "core":         1.0,
        "preferred":    0.6,
        "nice_to_have": 0.2
    }

    gap_score    = 0.0
    max_possible = 0.0
    skill_details = []

    for row in rows:
        importance_mult = importance_weights.get(row["importance"], 0.2)
        req_w           = row["required_weight"]
        your_p          = row["your_proficiency"]
        status          = row["status"]
        skill_name      = row["skill"]

        # raw gap contribution
        if status == "missing":
            raw_gap = 1.0 * req_w
        elif status == "partial":
            raw_gap = 0.5 * req_w
        else:
            raw_gap = 0.0

        # prerequisite reduction — if you own prereqs, gap is less severe
        prereqs = prereq_coverage.get(skill_name, 0)
        prereq_reduction = min(prereqs * 0.15, 0.4)  # cap at 40% reduction
        adjusted_gap = raw_gap * (1.0 - prereq_reduction)

        weighted_gap  = adjusted_gap  * importance_mult
        weighted_max  = req_w         * importance_mult

        gap_score    += weighted_gap
        max_possible += weighted_max

        skill_details.append({
            "skill":           skill_name,
            "category":        row["category"],
            "importance":      row["importance"],
            "status":          status,
            "your_proficiency": your_p,
            "required_weight": req_w,
            "prereqs_owned":   prereqs,
            "gap_contribution": round(weighted_gap, 3)
        })

    # ── Step 5: fit score ──
    fit_score = (
        1.0 - (gap_score / max_possible)
        if max_possible > 0 else 1.0
    )

    # domain alignment bonus — up to +5% on fit score
    domain_bonus = min(shared_domains * 0.025, 0.05)
    fit_score    = min(fit_score + domain_bonus, 1.0)

    # ── Step 6: prioritized learning roadmap ──
    roadmap = _build_roadmap(skill_details)

    return {
        "user_id":        user_id,
        "job_id":         job_id,
        "fit_score":      round(fit_score, 3),
        "gap_score":      round(gap_score, 3),
        "domain_bonus":   domain_bonus,
        "skill_breakdown": skill_details,
        "roadmap":        roadmap
    }


def _build_roadmap(skill_details: list[dict]) -> list[dict]:
    # only skills that aren't fully met
    gaps = [s for s in skill_details if s["status"] != "met"]

    # sort: importance tier first, then by gap contribution descending
    tier_order = {"core": 0, "preferred": 1, "nice_to_have": 2}

    gaps.sort(key=lambda s: (
        tier_order.get(s["importance"], 3),
        -s["gap_contribution"]
    ))

    roadmap = []
    for rank, gap in enumerate(gaps, start=1):
        # derive priority label from tier + prereq coverage
        if gap["importance"] == "core" and gap["prereqs_owned"] == 0:
            priority = "high"
        elif gap["importance"] == "core" and gap["prereqs_owned"] > 0:
            priority = "medium"  # you have prereqs, easier to close
        elif gap["importance"] == "preferred":
            priority = "medium"
        else:
            priority = "low"

        roadmap.append({
            "rank":          rank,
            "skill":         gap["skill"],
            "priority":      priority,
            "status":        gap["status"],
            "importance":    gap["importance"],
            "prereqs_owned": gap["prereqs_owned"],
            "reason":        _reason(gap)
        })

    return roadmap


def _reason(gap: dict) -> str:
    if gap["status"] == "missing" and gap["prereqs_owned"] > 0:
        return (
            f"Not in your profile but you own "
            f"{gap['prereqs_owned']} prerequisite(s) — quick to learn"
        )
    if gap["status"] == "missing" and gap["importance"] == "core":
        return "Core requirement — not in your profile, address first"
    if gap["status"] == "missing" and gap["importance"] == "nice_to_have":
        return "Nice to have — low urgency"
    if gap["status"] == "partial":
        return (
            f"You have this at {gap['your_proficiency']:.0%} "
            f"but {gap['required_weight']:.0%} is expected"
        )
    return "Gap identified"