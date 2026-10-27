# Nextnode

**[▶ Live interactive demo](https://gargi-09.github.io/NextNode/)** · **[🧪 Feature sandbox](https://gargi-09.github.io/NextNode/sandbox.html)**: run the agent simulation, explore the career graph, and try every API feature in your browser, with nothing to install. Running the backend locally? The same pages are at `http://localhost:8000/demo/`, where the sandbox's Live mode calls your API.

AI-powered career intelligence tool that thinks in graphs. Paste your resume and a job description, get back a personalized fit score, skill gap breakdown, learning roadmap, and career pivot analysis — all powered by a neurosymbolic reasoning engine built on a knowledge graph.

## Design

**[docs/DESIGN.md](docs/DESIGN.md)**: one page with the component diagram, the neural/symbolic split, how each failure mode is detected and handled, the tradeoffs (with measured evidence), limits, and what is not built. Phase-level details: [graph and scoring](docs/TDD-phase1-2.md), [MCP server and agents](docs/TDD-phase3-omnigent.md), [self-hosted inference](docs/TDD-phase4-vllm.md).

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
- **MCP (Model Context Protocol)** — 7 graph tools for AI agents
- **Omnigent, Ollama, vLLM, Kubernetes** — multi-agent workflow and self-hosted models (Qwen3-4B, Llama 3.1)

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
git clone https://github.com/gargi-09/NextNode
cd NextNode
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
├── deploy/k8s/       # vLLM manifests (vllm/) and the local CPU rehearsal overlay (vllm-local/)
├── deploy/aws/       # EKS cluster config (written, not deployed)
├── scripts/          # seed builder, page generator, agent runner, smoke tests, startup scripts
└── tests/
```

## Neo4j schema

Nodes: `Skill`, `User`, `JobPosting`, `Domain`

Key relationships: `HAS_SKILL` (proficiency), `REQUIRES_SKILL` (importance + weight),
`PREREQUISITE_OF` (strength), `BELONGS_TO` (skill → domain), `TARGETS`, `IN_DOMAIN`

## Measured results

Only numbers measured in this repo or on my laptop; nothing estimated.

| What | Result |
|---|---|
| Automated tests | 184 passing (run on every push by GitHub Actions) |
| Ontology | 60 skills, 107 aliases, 46 prerequisite edges, 4 domains |
| Pages demo vs. Python engine | Scores match exactly (parity test runs the JavaScript port under Node) |
| vLLM on a local Kubernetes cluster (CPU, Qwen3-0.6B) | Smoke test 5/5: key enforced, model name, chat, structured tool call |
| Qwen3-4B (Ollama) driving the real MCP tools | Called `calculate_skill_gap` with the right ids and reported the graph's exact fit score |
| Full agent run on a laptop CPU (Qwen3-4B + Llama 3.1) | 1,238–1,246 s for the two complete runs with a revision |
| Omnigent orchestrator on Qwen3-4B | Stalled after dispatching in 2 of 2 runs, which is why orchestration moved into code |
| Llama 3.1 8B as reviewer | Reported all 7 checks failing on a curriculum the graph checks passed, so its findings are advisory |

## Limitations

- **The public demo runs in your browser.** It uses an in-browser copy of the API and the
  scoring engine with sample data; it does not touch Neo4j or any model. The live path (FastAPI,
  Neo4j, Gemini, agents) runs locally.
- **Not deployed to the cloud.** The EKS config is written but no cluster exists, and Cloud Run is
  not set up.
- **Not tested under real traffic.** Single user, local runs only.
- **No auth or multi-tenancy on the REST API**; it is meant for local use. Only the MCP server
  requires a token.
- **Small local models are slow and imperfect.** A full agent run takes about 20 minutes on CPU,
  and the 4B parser underrates some skills.

## Status

Phases 1–3 built and tested; Phase 4 (self-hosted inference) rehearsed locally, cloud
deployment pending.
