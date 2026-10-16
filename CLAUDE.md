# NextNode — project notes for Claude

## Definition of done: keep the Features dashboard current
Every completed task that adds, changes or removes a feature must update
`frontend/src/features/features.json` in the same change:
- add or edit the feature entry (plain-English `summary` and `how`, `files`, `endpoints`,
  `status`: live | built | designed | planned) and a `tryIt` tester for live HTTP features;
- append a `changelog` entry (date, task, feature ids) and bump `updated`.

Then regenerate the public demo data: `python scripts/build_pages.py` (writes
`docs/assets/data.js`, used by `docs/index.html` and `docs/sandbox.html` on GitHub Pages).
- New or changed endpoint: add or update its route in `docs/assets/mock-api.js` (the sandbox's
  in-browser API) with the same response shape.
- Scoring or graph-logic change: update the port in `docs/assets/nextnode-core.js`.
- New agent: update the simulation in `docs/index.html`.

`tests/test_dashboard_registry.py` fails if an API route is missing from the dashboard,
a listed route no longer exists, or a tester spec doesn't match its route.
`tests/test_pages.py` fails if `data.js` is stale, if a registry endpoint has no mock route,
or if the mock API's scores drift from the Python engine (needs Node; skipped without it).

## Communication
Keep terminal replies short. Put detailed explanations and visuals in `docs/index.html`
or docs files rather than long chat text.

## Hard constraints
- Never read, log or modify `.env`, `.env.*` or any file holding secrets.
- Free-tier first: Gemini calls go through `app/extraction/llm.py` (limiter + cache); Neo4j
  calls go through `neo4j_client.read/write` (circuit breaker).
- LLM output never creates Skill or Domain nodes: resolve via `app/graph/ontology.py`.

## Commands
- Backend: `venv\Scripts\activate` then `uvicorn app.main:app --reload`
- Frontend: `npm --prefix frontend run dev` (http://localhost:5173)
- Tests: `venv\Scripts\python -m pytest` and `npm --prefix frontend run lint`
- Seed after editing `ontology/skills.json`: `python scripts/build_seed.py`
