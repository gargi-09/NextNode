# Nextnode

**[▶ Live interactive demo](https://gargi-09.github.io/NextNode/)** · **[🧪 Feature sandbox](https://gargi-09.github.io/NextNode/sandbox.html)**: run the agent simulation, explore the career graph, and try every API feature in your browser, with nothing to install. Running the backend locally? The same pages are at `http://localhost:8000/demo/`, where the sandbox's Live mode calls your API.

AI-powered career intelligence tool that thinks in graphs. Paste your resume and a job description, get back a personalized fit score, skill gap breakdown, learning roadmap, and career pivot analysis — all powered by a neurosymbolic reasoning engine built on a knowledge graph.

## What makes it different

Most career tools are keyword matchers. Nextnode models skills as a graph — with prerequisite relationships, domain clusters, and transferability weights — then runs deterministic symbolic rules over that graph to compute scores. The LLM handles unstructured extraction; the graph handles reasoning. Same input always produces the same output, and every score is explainable.

## Features

- **Job fit analysis** — fit score, domain alignment bonus, and per-skill breakdown with status (met / partial / missing)
- **Prerequisite-aware gap scoring** — missing a skill hurts less if you already own its prerequisites
- **Career pivot analyzer** — readiness score, effort estimate, transferable skills, and top skills to acquire for any target role
- **LLM explanation layer** — Gemini writes a plain-language coaching report grounded in the graph's output
- **Interactive skill graph** — D3 force-directed visualization of your skills, the job's requirements, and prerequisite edges
- **Dark / light mode**

## Stack

- **FastAPI** — async Python backend
- **Neo4j AuraDB** — knowledge graph (skills, jobs, users, domains, prerequisite chains)
- **Gemini 2.5 Flash** — skill extraction and explanation
- **React + Vite + Tailwind** — frontend
- **D3.js** — skill graph visualization
- **Google Cloud Run** — deployment target

## How it works

1. Gemini extracts structured skills from your resume and the job description
2. Skills, relationships, and proficiency scores are written to Neo4j as nodes and typed edges
3. A symbolic rule engine traverses the graph — walking prerequisite chains, applying importance weights, computing domain alignment — to produce a deterministic fit score
4. For pivot analysis, the same engine computes transferability-weighted gaps between your current skill set and a target role
5. Gemini reads the structured output and writes a personalized explanation

## Architecture
[![Architecture diagram of gargi-09/nextnode](https://gitdiagram.com/gargi-09/nextnode/diagram.png)](https://gitdiagram.com/gargi-09/nextnode?utm_source=readme&utm_medium=picture)
<img width="6728" height="7156" alt="diagram (3)" src="https://github.com/user-attachments/assets/2d38f599-49f1-4330-b805-033e59262707" />


## Setup

```bash
git clone https://github.com/yourusername/nextnode
cd nextnode
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file:

```
NEO4J_URI=your_uri
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
GEMINI_API_KEY=your_key
```

Initialize the graph (Neo4j Browser / Aura console, in order — all scripts are safe to re-run):
1. `cypher/constraints.cypher` — uniqueness constraints and indexes
2. `cypher/seed.cypher` — the canonical skill ontology (generated, see below)
3. Only for graphs that already hold data from older versions:
   - `cypher/migrations/001_dedupe_edges.cypher` — removes duplicate edges
   - `python scripts/migrate_canonical_skills.py` (dry run), then `--apply` — folds alias
     skill nodes into canonical ones and moves unknown skills into `unmapped_skills`

`cypher/reference_queries.cypher` holds ad-hoc inspection queries.

### Skill ontology

`ontology/skills.json` is the single source of truth for skills, aliases, prerequisites
and domains. Skill names are resolved strictly (exact name or listed alias, no fuzzy
matching); anything else is stored in the user's or job's `unmapped_skills` list and
never becomes a graph node. To add a skill or alias:

```bash
python scripts/build_seed.py
```

then re-run `cypher/seed.cypher`. `GET /api/v1/ontology/unmapped` lists the most common
unresolved terms to review.

### Free-tier limits

Gemini calls go through a global limiter (`GEMINI_RPM`, `GEMINI_RPD`, defaults 8 / 200 —
set them from your AI Studio console) and a content-hash cache, so identical text is never
parsed twice. When the budget is spent the API answers **429** with `Retry-After`;
explanations stop at 80% of the daily budget so parsing keeps working. If Aura is paused,
requests answer **503** with `Retry-After` and a resume hint, and a circuit breaker stops
retrying for 30 s after 3 failures. `GET /health/graph` shows graph status, size and
today's Gemini usage.

Run locally:
```bash
uvicorn app.main:app --reload
```

Run the frontend (http://localhost:5173):

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

No-install demo: `docs/index.html` and `docs/sandbox.html` (GitHub Pages), also served by
the API at http://localhost:8000/demo/.

Interactive API docs: http://localhost:8000/docs

### API overview (`/api/v1`)

| Method | Path | LLM | Purpose |
|---|---|---|---|
| POST | `/users` | – | Create a user (`{"id"?, "name", "target_domain"?}`); 409 if the id exists |
| GET | `/users`, `/users/{id}` | – | List users / user with skills |
| PUT | `/users/{id}/skills` | – | Replace the full skill list by hand (resolved against the ontology) |
| POST | `/users/{id}/resume` | ✓ | Parse a resume; updates resume skills, keeps manual ones; unchanged text is skipped |
| DELETE | `/users/{id}` | – | Delete the user and their edges |
| POST | `/jobs` | – | Create a job from structured skills; 409 if the id exists |
| POST | `/jobs/from-description` | ✓ | Create a job by parsing JD text |
| GET | `/jobs`, `/jobs/{id}` | – | List jobs / job with requirements |
| DELETE | `/jobs/{id}` | – | Delete the job and its edges |
| GET | `/analyze/{user_id}/{job_id}?explain=false` | optional | Fit score, breakdown, roadmap, plus `graph` (react-force-graph) and `roadmap_flow` (@xyflow/react) |
| GET | `/ontology/graph` | – | Full ontology as react-force-graph data (served from the JSON file) |
| GET | `/ontology/resolve?names=a,b` | – | Resolve names to canonical skills (no writes) |
| GET | `/ontology/unmapped` | – | Unresolved skill terms, most frequent first |
| POST | `/pivot` | ✓ | Career pivot analysis |

`/extract/jd` and `/extract/resume` still work as before.

Quick check without using any Gemini quota:
```bash
curl -X POST localhost:8000/api/v1/users -H "Content-Type: application/json" -d '{"id":"u1","name":"Test User","target_domain":"nlp"}'
curl -X PUT localhost:8000/api/v1/users/u1/skills -H "Content-Type: application/json" -d '{"skills":[{"name":"Python","proficiency":0.9},{"name":"PyTorch","proficiency":0.4}]}'
curl -X POST localhost:8000/api/v1/jobs -H "Content-Type: application/json" -d '{"id":"j1","title":"ML Engineer","core_skills":[{"name":"Python","weight":0.7},{"name":"PyTorch","weight":0.7},{"name":"LLM fine-tuning","weight":0.6}],"domains":["nlp"]}'
curl "localhost:8000/api/v1/analyze/u1/j1?explain=false"
```

Run tests (no database or API key needed):
```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Project structure

```
nextnode/
├── app/
│   ├── api/          # FastAPI routes + error mapping
│   ├── core/         # settings, errors
│   ├── db/           # Neo4j client (managed transactions, circuit breaker)
│   ├── extraction/   # Gemini client, rate limiter, cache, prompts
│   ├── graph/        # ontology, writer, reasoner, pivot, scoring, payloads
│   ├── models/       # Pydantic schemas
│   └── services/     # use cases called by the routes
├── cypher/           # constraints, generated seed, migrations
├── ontology/         # skills.json: the canonical skill vocabulary
├── frontend/         # React 19 + Vite UI
├── docs/             # GitHub Pages demo + sandbox, design docs (TDDs)
├── omnigent/         # multi-agent workflow configs and policies
├── deploy/k8s/vllm/  # self-hosted model server manifests (Phase 4)
├── scripts/          # seed builder, migrations, page generator, startup scripts
└── tests/
```

## Neo4j schema

Nodes: `Skill`, `User`, `JobPosting`, `Domain`

Key relationships: `HAS_SKILL` (proficiency), `REQUIRES_SKILL` (importance + weight),
`PREREQUISITE_OF` (strength), `BELONGS_TO` (skill → domain), `TARGETS`, `IN_DOMAIN`

## Status
Work in progress.
