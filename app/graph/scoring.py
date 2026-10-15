"""Pure symbolic scoring rules.

No database or LLM imports here — every function takes plain data and returns
plain data, so it can be unit-tested and reused by API routes or MCP tools alike.
"""

# Bump when any rule below changes, so clients can tell scores apart.
SCORING_VERSION = "1"

GAP_IMPORTANCE_WEIGHTS = {
    "core":         1.0,
    "preferred":    0.6,
    "nice_to_have": 0.2
}

PIVOT_IMPORTANCE_WEIGHTS = {"core": 1.0, "preferred": 0.6}

TIER_ORDER = {"core": 0, "preferred": 1, "nice_to_have": 2}


# ─────────────────────────── job fit ───────────────────────────

def score_gap(
    user_id: str,
    job_id: str,
    rows: list[dict],
    shared_domains: int,
    prereq_coverage: dict[str, int],
) -> dict:
    """rows: one dict per required skill with keys
    skill, category, importance, required_weight, your_proficiency, status."""
    gap_score     = 0.0
    max_possible  = 0.0
    skill_details = []

    for row in rows:
        importance_mult = GAP_IMPORTANCE_WEIGHTS.get(row["importance"], 0.2)
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

    fit_score = (
        1.0 - (gap_score / max_possible)
        if max_possible > 0 else 1.0
    )

    # domain alignment bonus — up to +5% on fit score
    domain_bonus = min(shared_domains * 0.025, 0.05)
    fit_score    = min(fit_score + domain_bonus, 1.0)

    return {
        "user_id":        user_id,
        "job_id":         job_id,
        "fit_score":      round(fit_score, 3),
        "gap_score":      round(gap_score, 3),
        "domain_bonus":   domain_bonus,
        "skill_breakdown": skill_details,
        "roadmap":        build_roadmap(skill_details)
    }


def build_roadmap(skill_details: list[dict]) -> list[dict]:
    # only skills that aren't fully met
    gaps = [s for s in skill_details if s["status"] != "met"]

    # sort: importance tier first, then by gap contribution descending
    gaps.sort(key=lambda s: (
        TIER_ORDER.get(s["importance"], 3),
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


# ─────────────────────────── career pivot ───────────────────────────

def score_pivot(
    user_id: str,
    extracted,  # ExtractedPivotRole
    user_skills: dict[str, float],
    jd_skills: dict[str, float],
) -> dict:
    all_target_skills = [
        (s, "core") for s in extracted.core_skills
    ] + [
        (s, "preferred") for s in extracted.preferred_skills
    ]

    gap_score     = 0.0
    max_possible  = 0.0
    skill_details = []
    transferable  = []
    gaps          = []

    for skill, importance in all_target_skills:
        imp_w    = PIVOT_IMPORTANCE_WEIGHTS[importance]
        user_p   = user_skills.get(skill.name, 0.0)
        jd_p     = jd_skills.get(skill.name, 0.0)
        req_p    = skill.proficiency
        transfer = skill.transferability

        # use max of resume proficiency and JD evidence
        effective_p = max(user_p, jd_p * 0.9)

        if effective_p >= req_p:
            status = "met"
            raw_gap = 0.0
        elif effective_p > 0:
            status = "partial"
            raw_gap = (req_p - effective_p) * req_p
        else:
            status = "missing"
            raw_gap = req_p

        adjusted_gap = raw_gap * (1.0 - (transfer * 0.4))
        weighted_gap = adjusted_gap * imp_w
        weighted_max = req_p * imp_w

        gap_score    += weighted_gap
        max_possible += weighted_max

        detail = {
            "skill":            skill.name,
            "category":         skill.category,
            "importance":       importance,
            "status":           status,
            "your_proficiency": effective_p,
            "required":         req_p,
            "transferability":  transfer,
            "from_jd":          jd_p > 0 and jd_p >= user_p,
            "gap_contribution": round(weighted_gap, 3)
        }
        skill_details.append(detail)

        if status == "met" and transfer >= 0.7:
            transferable.append(skill.name)
        elif status != "met":
            gaps.append(detail)

    pivot_score = (
        1.0 - (gap_score / max_possible)
        if max_possible > 0 else 1.0
    )

    # ── role overlap (only when JD provided) ──
    role_overlap_pct = None
    if jd_skills:
        target_skill_names = {s.name for s, _ in all_target_skills}
        overlap = len(set(jd_skills.keys()) & target_skill_names)
        role_overlap_pct = round(overlap / max(len(target_skill_names), 1) * 100)

    # ── effort estimate ──
    if pivot_score >= 0.75:
        effort = "low"
        effort_label = "You're already close — mostly upskilling"
    elif pivot_score >= 0.50:
        effort = "medium"
        effort_label = "Achievable in 6–12 months of focused learning"
    else:
        effort = "high"
        effort_label = "Significant reskilling required — 12–24 months"

    gaps.sort(key=lambda s: (
        0 if s["importance"] == "core" else 1,
        -s["gap_contribution"]
    ))

    return {
        "user_id":             user_id,
        "target_role":         extracted.role_title,
        "pivot_score":         round(pivot_score, 3),
        "effort":              effort,
        "effort_label":        effort_label,
        "transferable_skills": transferable,
        "skill_gaps":          gaps[:6],
        "skill_details":       skill_details,
        "domains":             extracted.domains,
        "role_overlap_pct":    role_overlap_pct,
        "jd_context_used":     bool(jd_skills)
    }
