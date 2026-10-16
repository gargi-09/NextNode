"""Generate docs/assets/data.js for the static demo pages (GitHub Pages).

    python scripts/build_pages.py          # rewrite docs/assets/data.js
    python scripts/build_pages.py --check  # exit 1 if it is out of date

docs/index.html (pitch dashboard) and docs/sandbox.html (feature sandbox) load it with a
plain <script> tag: no build step and no fetches, so both work from GitHub Pages and from
a double-click on the file. Everything they show comes from:
  * frontend/src/features/features.json  (features + changelog)
  * ontology/skills.json                 (skills, aliases, prerequisite chains)
  * SCENARIOS below, whose expected results are computed here with the REAL Python
    scoring code, so the page's in-browser self-test proves its JS port matches.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.graph.ontology import load_ontology  # noqa: E402
from app.graph.scoring import SCORING_VERSION, score_gap  # noqa: E402

DATA_JS = ROOT / "docs" / "assets" / "data.js"
FEATURES = ROOT / "frontend" / "src" / "features" / "features.json"

# Role profiles the sandbox's /pivot endpoint uses instead of asking Gemini.
# [canonical skill, expected proficiency, transferability]
ROLE_TEMPLATES = {
    "MLOps Engineer": {"domains": ["ml_infrastructure"],
        "core": [["Docker", 0.7, 0.7], ["Kubernetes", 0.6, 0.6], ["MLOps", 0.7, 0.5], ["Python", 0.7, 1.0]],
        "preferred": [["MLflow", 0.5, 0.6], ["AWS", 0.5, 0.7], ["Apache Airflow", 0.4, 0.6]]},
    "Data Scientist": {"domains": ["data_engineering"],
        "core": [["Python", 0.7, 1.0], ["Statistics", 0.7, 0.9], ["Machine Learning", 0.7, 0.8], ["SQL", 0.6, 0.9]],
        "preferred": [["Pandas", 0.6, 0.8], ["scikit-learn", 0.6, 0.8], ["Tableau", 0.4, 0.6]]},
    "NLP Engineer": {"domains": ["nlp"],
        "core": [["Python", 0.8, 1.0], ["Natural Language Processing", 0.7, 0.6], ["PyTorch", 0.7, 0.8],
                 ["HuggingFace Transformers", 0.7, 0.6]],
        "preferred": [["LLM fine-tuning", 0.5, 0.5], ["Retrieval-Augmented Generation", 0.5, 0.6], ["spaCy", 0.4, 0.5]]},
    "Computer Vision Engineer": {"domains": ["computer_vision"],
        "core": [["Python", 0.8, 1.0], ["Computer Vision", 0.7, 0.5], ["Deep Learning", 0.7, 0.8], ["PyTorch", 0.7, 0.8]],
        "preferred": [["OpenCV", 0.6, 0.5], ["Docker", 0.4, 0.7]]},
}

# Sample candidates and jobs. `skills` is what the Parser agent would extract from
# `resume` (aliases and unknown terms left in on purpose to show strict resolution).
SCENARIOS = [
    {
        "id": "llm-platform",
        "label": "ML engineer → LLM platform role",
        "candidate": {
            "name": "Priya (ML engineer)", "target_domain": "nlp",
            "resume": "ML engineer, 4 years of production Python. Trained models in torch, built search "
                      "on Elasticsearch, comfortable with SQL and docker compose. Hobby: basket weaving.",
            "skills": [["python", 0.9], ["torch", 0.4], ["Elasticsearch", 0.8], ["SQL", 0.7],
                       ["docker compose", 0.5], ["basket weaving", 0.6]],
        },
        "job": {
            "title": "ML Engineer, LLM Platform", "company": "Acme AI", "domains": ["nlp", "ml_infrastructure"],
            "core": [["Python", 0.7], ["PyTorch", 0.7], ["LLM fine-tuning", 0.6], ["HuggingFace Transformers", 0.6]],
            "preferred": [["Vector Databases", 0.5], ["RAG", 0.5]],
            "nice": [["Docker", 0.4], ["k8s", 0.3]],
        },
    },
    {
        "id": "data-eng",
        "label": "Analyst → Data engineer",
        "candidate": {
            "name": "Marco (data analyst)", "target_domain": "data_engineering",
            "resume": "Data analyst. Daily SQL on postgres, Python and pandas for reporting, dashboards in "
                      "Tableau, a little pyspark in a course.",
            "skills": [["SQL", 0.8], ["postgres", 0.8], ["python", 0.7], ["pandas", 0.7],
                       ["Tableau", 0.6], ["pyspark", 0.3]],
        },
        "job": {
            "title": "Data Engineer", "company": "Northwind", "domains": ["data_engineering"],
            "core": [["SQL", 0.7], ["Apache Spark", 0.6], ["Airflow", 0.6]],
            "preferred": [["Snowflake", 0.5], ["Kafka", 0.5]],
            "nice": [["Databricks", 0.4], ["Docker", 0.3]],
        },
    },
    {
        "id": "cv-pivot",
        "label": "Backend dev → Computer vision",
        "candidate": {
            "name": "Sam (backend developer)", "target_domain": "ml_infrastructure",
            "resume": "Backend developer: Python and FastAPI services on Linux, some NumPy, a TensorFlow "
                      "course (tf 2), OpenCV side project, Go microservices.",
            "skills": [["Python", 0.8], ["FastAPI", 0.8], ["Linux", 0.7], ["NumPy", 0.6],
                       ["tf", 0.5], ["OpenCV", 0.4], ["golang", 0.6]],
        },
        "job": {
            "title": "Computer Vision Engineer", "company": "Visionary Labs", "domains": ["computer_vision"],
            "core": [["PyTorch", 0.7], ["Computer Vision", 0.7], ["Deep Learning", 0.6]],
            "preferred": [["OpenCV", 0.6], ["Docker", 0.4]],
            "nice": [["Kubernetes", 0.3]],
        },
    },
]


def _expected(scenario, ontology, nxt):
    """Run the real Python rules on a scenario (mirrors the Cypher queries)."""
    owned = {}
    for name, p in scenario["candidate"]["skills"]:
        s = ontology.resolve(name)
        if s:
            owned[s.name] = max(p, owned.get(s.name, 0))

    rows, seen = [], set()
    for tier, key in (("core", "core"), ("preferred", "preferred"), ("nice_to_have", "nice")):
        for name, w in scenario["job"][key]:
            s = ontology.resolve(name)
            if not s or s.name in seen:
                continue
            seen.add(s.name)
            p = owned.get(s.name)
            status = "missing" if p is None else ("met" if p >= w else "partial")
            rows.append({"skill": s.name, "category": s.category, "importance": tier,
                         "required_weight": w, "your_proficiency": p or 0.0, "status": status})

    def reach(src):
        out, frontier = set(), {src}
        for _ in range(3):
            frontier = {t for f in frontier for t in nxt.get(f, ())}
            out |= frontier
        return out

    prereq = {r["skill"]: sum(1 for o in owned if r["skill"] in reach(o))
              for r in rows if r["status"] == "missing"}
    target = ontology.resolve_domain(scenario["candidate"]["target_domain"])
    shared = 1 if target and target in scenario["job"]["domains"] else 0
    result = score_gap("u", "j", rows, shared, prereq)
    return {"fit_score": result["fit_score"], "gap_score": result["gap_score"],
            "domain_bonus": result["domain_bonus"],
            "roadmap": [r["skill"] for r in result["roadmap"]]}


def _roles(ontology) -> list:
    roles = []
    for title, t in ROLE_TEMPLATES.items():
        def skills(rows):
            out = []
            for name, prof, transfer in rows:
                s = ontology.resolve(name)
                assert s and s.name == name, f"role template skill {name!r} is not canonical"
                out.append({"name": s.name, "category": s.category, "level": s.level,
                            "proficiency": prof, "transferability": transfer})
            return out
        roles.append({"role_title": title, "domains": t["domains"],
                      "core_skills": skills(t["core"]), "preferred_skills": skills(t["preferred"])})
    return roles


def build_data() -> dict:
    ontology = load_ontology()
    registry = json.loads(FEATURES.read_text(encoding="utf-8"))
    nxt = {}
    for p in ontology.prerequisites:
        nxt.setdefault(p.source, set()).add(p.target)

    skills = [{"key": s.key, "name": s.name, "category": s.category, "level": s.level,
               "domains": list(s.domains)} for s in sorted(ontology.skills.values(), key=lambda s: s.key)]
    return {
        "updated": registry["updated"],
        "scoringVersion": SCORING_VERSION,
        "ontology": {
            "version": ontology.version,
            "skills": skills,
            "aliases": dict(sorted(ontology.aliases.items())),
            "domains": sorted(ontology.domains),
            "prerequisites": sorted([[p.source, p.target, p.strength] for p in ontology.prerequisites]),
        },
        "features": registry["features"],
        "roles": _roles(ontology),
        "changelog": registry["changelog"],
        "scenarios": [{**s, "expected": _expected(s, ontology, nxt)} for s in SCENARIOS],
    }


def render() -> str:
    data = json.dumps(build_data(), ensure_ascii=False, indent=1)
    header = "// GENERATED by scripts/build_pages.py: do not edit by hand."
    return f"{header}\nwindow.NEXTNODE = {data};\n"


if __name__ == "__main__":
    updated = render()
    current = DATA_JS.read_text(encoding="utf-8") if DATA_JS.exists() else ""
    if "--check" in sys.argv:
        if updated != current:
            print("docs/assets/data.js is out of date: run python scripts/build_pages.py")
            sys.exit(1)
        print("docs/assets/data.js is up to date")
    else:
        DATA_JS.parent.mkdir(parents=True, exist_ok=True)
        DATA_JS.write_text(updated, encoding="utf-8")
        print("wrote docs/assets/data.js")
