# NextNode roadmap: from portfolio project to a product organizations pilot

**Goal:** a live, safe, cheap-to-run career skill-gap engine that bootcamps, university career
services and upskilling platforms pilot with real learners, measured by real usage.

Today NextNode is a working local system with a public in-browser demo (see the
[README](../README.md) and [DESIGN.md](DESIGN.md)). This page lists what stands between that and a
pilot, in order, with a definition of done for each step.

## Principles

- **Every claim is measured.** Pitches, the README and the app quote only numbers from this repo's
  tests, evals or production counters. No estimates presented as results.
- **The LLM never decides.** Models parse text; the ontology resolves names; graph rules score and
  verify. This is the product's core promise, so it stays true in every mode.
- **Cheap by default.** Scale to zero, one small instance, free tiers where the terms allow it, a
  budget alert on every paid service. Target: under $10 a month at pilot scale.
- **Privacy first.** Resumes are personal data: no raw resume text stored, a paid model tier that
  does not train on inputs, data that expires, a plain-language data page.

## What we can claim today (and what not yet)

| Safe to say | Not yet true |
|---|---|
| An LLM can never invent a skill or a score: unknown names go to a review queue, scoring is fixed rules over a graph | "Zero errors" or "no hallucination": parsing can still misjudge proficiency |
| Deterministic graph checks decide the verdict; in a 20-task eval they caught 2 bad plans and queued them | Real users or production traffic |
| 18/20 eval tasks as expected, live and in replay (0 drift); all 8 injected faults handled | Course recommendations from a real catalog |
| Same input, same score (`scoring_version` "1") | Batch analysis of a cohort; multi-tenant API |

## Milestones

### M1. Public, safe, live app (about 3 days)
- `PUBLIC_MODE`: only the analyze flow is exposed; write, delete and list endpoints are off;
  visitor data expires; per-IP rate limit; input size caps; CORS limited to the site.
- FastAPI on Cloud Run: scale to zero, 1 instance max (keeps the in-process limiter and cache
  honest), secrets in Secret Manager, a budget alert.
- The React app on GitHub Pages calling that API, falling back to the in-browser demo when the
  API is down or waking up.
- A daily health ping so Neo4j Aura Free never pauses; a privacy page.

**Done when** a stranger can open one link, paste a resume and a job, and get a scored roadmap,
and nobody can read or delete anyone else's data.

### M2. Usage you can measure (about 1 day)
- Privacy-safe counters: analyses run, unique sessions, return visits, parse failures, review-queue
  items, p50/p95 latency, model cost per analysis. No resume content in metrics.
- A small private stats page.

**Done when** weekly usage and cost numbers come from the counters, not from guesses.

### M3. A real-size ontology (about 1 week)
- Seed skills, aliases and prerequisites from public taxonomies (O*NET, ESCO; check each license
  first), mapped into the existing strict schema.
- Grow it from the review queue: unknown terms seen by real users become proposed aliases or
  skills for human approval.
- Keep the golden-score and parity tests passing; version the ontology.

**Done when** the ontology covers the target roles of the first pilot without manual patching.

### M4. Course catalog layer (about 1 week)
- Import a catalog (CSV or API): each course mapped to the skills it teaches and the skills it
  expects, through the same strict resolution.
- Roadmap modules recommend real courses, ordered by the prerequisite graph.

**Done when** a roadmap lists specific courses from an imported catalog, in a valid order.

### M5. B2B pilot API (about 1–2 weeks)
- An API key per organization, data isolated per organization.
- A batch endpoint: analyze a cohort of resumes against a role or a catalog.
- An aggregate cohort report (most common gaps, readiness by role).
- A data-handling page suitable for an organization's review.

**Done when** a partner can run a cohort through the API and get a report, with their data
separate from everyone else's.

## Pilots

Start where a yes is fast: coding bootcamps and university career services, then mid-sized
HR-tech and upskilling platforms, then large course marketplaces. For each pilot record the
agreement in writing (a short letter of intent), the cohort size, and the measured outcomes from
M2's counters.

## Kept as engineering depth, not needed to run the product

The multi-agent harness, Omnigent bundle, vLLM on Kubernetes and the EKS config stay in the repo
and keep their tests. The public product runs on the lean path (one Gemini parse plus the
deterministic engine); the agent stack is for heavier offline work and future features.
