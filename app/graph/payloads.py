"""Pure builders that turn gap-analysis results into frontend graph payloads.

No database access: inputs are the rows and edges already read in the analysis
transaction. Every link references a node in the same payload, and ids are
type-prefixed so a skill and a domain can never collide.
"""

_IMPORTANCE_SIZE = {"core": 4.0, "preferred": 3.0, "nice_to_have": 2.0}

FLOW_ROW_HEIGHT    = 140
FLOW_OWNED_X       = -320
FLOW_OWNED_SPACING = 90


def user_node_id(user_id: str) -> str:   return f"user:{user_id}"
def job_node_id(job_id: str) -> str:     return f"job:{job_id}"
def skill_node_id(name: str) -> str:     return f"skill:{name}"
def domain_node_id(key: str) -> str:     return f"domain:{key}"


def build_graph_payload(*, user_id: str, user_name: str | None, job_id: str,
                        job_title: str | None, skill_breakdown: list[dict],
                        prereq_paths: list[dict], required_prereq_edges: list[dict],
                        user_domains: list[str], job_domains: list[str]) -> dict:
    nodes: dict[str, dict] = {}
    links: dict[tuple, dict] = {}

    def add_link(link: dict):
        links.setdefault((link["source"], link["target"], link["type"]), link)

    user_id_ = user_node_id(user_id)
    job_id_  = job_node_id(job_id)
    nodes[user_id_] = {"id": user_id_, "label": user_name or user_id, "type": "user", "val": 8.0}
    nodes[job_id_]  = {"id": job_id_, "label": job_title or job_id, "type": "job", "val": 8.0}

    # required skills: the job → skill edges, plus user → skill where the user has it
    for s in skill_breakdown:
        sid = skill_node_id(s["skill"])
        has_it = s["status"] in ("met", "partial")
        nodes[sid] = {
            "id": sid, "label": s["skill"], "type": "skill", "status": s["status"],
            "importance": s["importance"],
            "proficiency": s["your_proficiency"] if has_it else None,
            "required_weight": s["required_weight"],
            "val": _IMPORTANCE_SIZE.get(s["importance"], 2.0),
        }
        add_link({"source": job_id_, "target": sid, "type": "REQUIRES_SKILL",
                  "importance": s["importance"], "weight": s["required_weight"]})
        if has_it:
            add_link({"source": user_id_, "target": sid, "type": "HAS_SKILL",
                      "proficiency": s["your_proficiency"]})

    # prerequisite paths from owned skills to missing ones (intermediate hops included)
    owned_proficiency = {p["owned"]: p["proficiency"] for p in prereq_paths}
    for path in prereq_paths:
        for edge in path["edges"]:
            for name in (edge["source"], edge["target"]):
                sid = skill_node_id(name)
                if sid not in nodes:
                    owned = name in owned_proficiency
                    nodes[sid] = {
                        "id": sid, "label": name, "type": "skill",
                        "status": "owned" if owned else "prerequisite",
                        "proficiency": owned_proficiency.get(name),
                        "val": 1.5,
                    }
            add_link({"source": skill_node_id(edge["source"]),
                      "target": skill_node_id(edge["target"]),
                      "type": "PREREQUISITE_OF", "strength": edge.get("strength")})

    for owned, proficiency in owned_proficiency.items():
        add_link({"source": user_id_, "target": skill_node_id(owned), "type": "HAS_SKILL",
                  "proficiency": proficiency})

    for edge in required_prereq_edges:
        add_link({"source": skill_node_id(edge["source"]),
                  "target": skill_node_id(edge["target"]),
                  "type": "PREREQUISITE_OF", "strength": edge.get("strength")})

    for key in user_domains:
        did = domain_node_id(key)
        nodes.setdefault(did, {"id": did, "label": key, "type": "domain", "val": 3.0})
        add_link({"source": user_id_, "target": did, "type": "TARGETS"})
    for key in job_domains:
        did = domain_node_id(key)
        nodes.setdefault(did, {"id": did, "label": key, "type": "domain", "val": 3.0})
        add_link({"source": job_id_, "target": did, "type": "IN_DOMAIN"})

    return {"nodes": list(nodes.values()), "links": list(links.values())}


def build_roadmap_flow(roadmap: list[dict], prereq_paths: list[dict]) -> dict:
    """Vertical roadmap: steps in rank order at x=0, owned skills that unlock them
    in a column on the left. Positions are deterministic, so the layout is stable."""
    nodes: list[dict] = []
    edges: list[dict] = []
    step_y: dict[str, float] = {}

    for step in roadmap:
        y = (step["rank"] - 1) * FLOW_ROW_HEIGHT
        step_y[step["skill"]] = y
        nodes.append({
            "id": f"step:{step['skill']}", "type": "roadmapStep",
            "position": {"x": 0, "y": y},
            "data": {"label": step["skill"], "rank": step["rank"],
                     "priority": step["priority"], "status": step["status"],
                     "importance": step["importance"], "reason": step["reason"],
                     "prereqs_owned": step["prereqs_owned"]},
        })

    for a, b in zip(roadmap, roadmap[1:]):
        edges.append({
            "id": f"seq:{a['rank']}-{b['rank']}",
            "source": f"step:{a['skill']}", "target": f"step:{b['skill']}",
            "type": "smoothstep", "data": {"kind": "sequence"},
        })

    # owned skill → each roadmap step it is a (transitive) prerequisite of
    unlocks: dict[str, list[str]] = {}
    proficiency: dict[str, float] = {}
    for p in prereq_paths:
        if p["target"] in step_y:
            targets = unlocks.setdefault(p["owned"], [])
            if p["target"] not in targets:
                targets.append(p["target"])
            proficiency[p["owned"]] = p["proficiency"]

    ordered = sorted(unlocks, key=lambda o: (min(step_y[t] for t in unlocks[o]), o))
    last_y = None
    for owned in ordered:
        y = min(step_y[t] for t in unlocks[owned])
        if last_y is not None and y < last_y + FLOW_OWNED_SPACING:
            y = last_y + FLOW_OWNED_SPACING
        last_y = y
        nodes.append({
            "id": f"owned:{owned}", "type": "ownedSkill",
            "position": {"x": FLOW_OWNED_X, "y": y},
            "data": {"label": owned, "proficiency": proficiency[owned], "status": "owned"},
        })
        for target in unlocks[owned]:
            edges.append({
                "id": f"unlocks:{owned}->{target}",
                "source": f"owned:{owned}", "target": f"step:{target}",
                "type": "smoothstep", "label": "prerequisite", "animated": True,
                "data": {"kind": "unlocks"},
            })

    return {"nodes": nodes, "edges": edges}
