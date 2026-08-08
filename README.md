# Nextnode

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

Run the backend:

```bash
uvicorn app.main:app --reload
```

Run the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`

## Project structure

```
nextnode/
├── app/
│   ├── api/          # FastAPI routes
│   ├── core/         # config, settings
│   ├── db/           # Neo4j driver
│   ├── extraction/   # Gemini prompts + extractor + explainer
│   ├── graph/        # reasoner + pivot analyzer + Neo4j writer
│   └── models/       # Pydantic schemas
├── frontend/
│   └── src/
│       ├── api/        # Axios client
│       ├── components/ # FitScore, SkillGraph, Roadmap, PivotAnalysis, etc.
│       └── pages/      # Home, Results
├── .env
├── requirements.txt
└── Dockerfile
```

## Neo4j schema

Nodes: `Skill`, `User`, `JobPosting`, `Domain`, `SkillCluster`

Key relationships: `HAS_SKILL` (with proficiency), `REQUIRES_SKILL` (with importance + weight), `PREREQUISITE_OF` (with strength), `BELONGS_TO`, `TARGETS`

## API endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/extract/jd` | Extract skills from job description |
| POST | `/api/v1/extract/resume` | Extract skills from resume |
| GET | `/api/v1/analyze/{user_id}/{job_id}` | Run gap analysis + explanation |
| POST | `/api/v1/pivot` | Career pivot analysis |
| GET | `/health` | Health check |
