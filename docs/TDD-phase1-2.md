# NextNode — Technical Design Document: Phase 1 (Graph Correctness) & Phase 2 (Dynamic Ingestion)

| | |
|---|---|
| Status | Phase 1 implemented · Phase 2 steps 2.2–2.5, 2.7 and the /analyze graph payloads implemented (ontology is JSON, not YAML; tenancy (2.6), LLM contract v2 enums (2.3), role profiles (2.9) and lexical fallback (2.10) still open) |
| Scope | Backend (`app/`), graph (`cypher/`), runtime limits for the $0 deployment |
| Out of scope | Frontend redesign, MCP server code (Phase 4), paid-tier scaling |
| Last reviewed | 2026-10-15 |

---

## 0. Executive summary

NextNode differentiates from flat skills taxonomies with a **skills ontology**: a graph of canonical skills, prerequisite chains and domains, over which skill gaps are computed deterministically. This document specifies:

1. **Phase 1:** the as-built fixes for graph-corruption bugs G1–G5, plus residual defects found in this review.
2. **Canonical skill normalization:** a strict, ontology-as-code resolver that stops LLM output from creating junk `Skill` nodes.
3. **Phase 2:** tenant-scoped User/JobPosting ingestion that is idempotent, cache-first and quota-aware.
4. **Runtime resilience:** surviving Gemini free-tier quotas and Neo4j Aura Free pausing, on AWS Lambda.

**Four decisions change the original brief:**

| # | Brief assumed | Reality (see §1) | Decision |
|---|---|---|---|
| D1 | API Gateway is free | API Gateway's free tier lasts **12 months only** | Use **Lambda Function URLs** (free under Lambda's always-free allowance). |
| D2 | Gemini quota is "per user" | Quotas are **per Google Cloud project**, shared by every tenant and user | Budget one global daily quota and divide it among tenants. |
| D3 | Aura Free has 200k nodes | Neo4j's own page states **50k nodes / 175k relationships**; third-party sources say 200k/400k | Design for **50k / 175k**. **Relationships, not nodes, run out first** (§8). |
| D4 | All gap maths in Cypher | Deterministic ≠ Cypher-only | Cypher does traversal (gap set, prerequisite reachability); a pinned, pure-Python rule set does the arithmetic. Both sit in the Domain layer (§2.2). |

**Commercial caveat to raise before any pitch:** free-tier Gemini prompts may be used to improve Google's models (outside the EU, Switzerland and the UK). Enterprise resumes are PII. The $0 tier is a **demo/pilot environment** only. Any real tenant data needs the paid Gemini tier (or a no-training provider) and a paid Aura tier with backups. Aura Free includes no backups.

---

## 1. Constraint verification

All figures were checked on 2026-10-15. Several sources disagree, so every limit must be a **config value**, never a code constant.

| Service | Verified limit | Design consequence |
|---|---|---|
| Gemini API free tier | Per project. Sources disagree for 2.5 Flash: 10 RPM / 250 RPD (Jan 2026) vs. about 1,500 RPD (Jul 2026). Flash-Lite is higher. The authoritative number is the AI Studio console. RPD resets at midnight Pacific. | Global limiter keyed on the Pacific date. `GEMINI_RPM` and `GEMINI_RPD` as settings. Degrade gracefully when the quota is spent. |
| Neo4j Aura Free | 1 instance. Official page: 50k nodes / 175k relationships. Pauses after about 3 days of inactivity. **Deleted after 30 days paused or inactive.** No backups. | Ontology lives in version control (rebuildable). Connections tolerate a paused instance. A heartbeat keeps the instance active. Periodic export of tenant data. |
| AWS Lambda | 1M requests + 400k GB-s per month, **always free** | Primary compute. |
| API Gateway | 1M calls per month, **first 12 months only** | Not used. Function URLs instead, with IAM or app-level auth. |
| Upstash Redis free | 500k commands/month (about 16k/day), 256 MB, 1 database | Budget at most 3 commands per LLM-bound request. No Redis calls on pure graph reads. |
| AWS Amplify Hosting | Not verified here; Amplify's free tier has historically been 12-month | Verify before committing. Permanent-free fallback: any static host for the Vite build. |

---

## 2. Target architecture: the four-layer model

```
┌──────────────────────────────────────────────────────────────────────┐
│ INTERFACE        REST (FastAPI)  ·  MCP server  ·  auth  ·  DTOs       │
├──────────────────────────────────────────────────────────────────────┤
│ APPLICATION      use cases · LLM parsing · cache · rate limiter ·     │
│                  ingestion orchestration · explanation narration      │
├──────────────────────────────────────────────────────────────────────┤
│ DOMAIN           ontology model · canonical resolver · Cypher query    │
│ (deterministic)  catalog · scoring rules · roadmap rules              │
├──────────────────────────────────────────────────────────────────────┤
│ INFRASTRUCTURE   Neo4j driver + circuit breaker · Gemini client ·     │
│                  Redis client · settings · clock                      │
└──────────────────────────────────────────────────────────────────────┘
```

**Dependency rule:** layers only call downward. The Domain layer imports nothing from Application or Interface, and never imports an LLM client.

### 2.1 Mapping from the current code

| Current file | Target layer and module | Notes |
|---|---|---|
| `app/api/routes.py` | `interface/rest/routes.py` | Already thin after Phase 1. |
| `app/models/schemas.py` | Split: `interface/dto.py` (requests/responses), `application/llm/contracts.py` (LLM output), `domain/model.py` (entities) | LLM output and domain entities are separate types today only by name. |
| `app/services/*` | `application/use_cases/*` | Already async orchestration. |
| `app/extraction/*` | `application/llm/*` | The only place LLM calls are allowed. |
| `app/graph/scoring.py`, `normalize.py` | `domain/scoring.py`, `domain/normalize.py` | Pure; no change in behavior. |
| Cypher strings in `writer.py`, `reasoner.py`, `pivot.py` | `domain/queries.py` (Cypher text) + `infrastructure/graph/repository.py` (execution) | Satisfies "Cypher in the Domain layer" while keeping the driver in Infrastructure. |
| `app/db/neo4j.py`, `app/core/config.py` | `infrastructure/graph/driver.py`, `infrastructure/settings.py` | Becomes lazy and resilient (§7). |

Move in small steps, leaving re-export shims at the old paths so the 18 existing tests stay green after every step.

### 2.2 The determinism boundary

| Concern | Layer | Deterministic? |
|---|---|---|
| Parse unstructured text into a candidate skill list | Application (LLM) | No. Contained by caching (§6.2) and enum outputs (§4.4). |
| Map candidate names to canonical skills | Domain (resolver) | Yes. Pure function over a versioned ontology. |
| Compute the gap set (met, partial, missing) | Domain (Cypher) | Yes. |
| Prerequisite reachability (`PREREQUISITE_OF*1..3`) | Domain (Cypher) | Yes. |
| Score arithmetic, roadmap ordering | Domain (Python, `scoring.py`) | Yes. Pure, version-pinned, returned as `scoring_version`. |
| Narrative explanation | Application (LLM) | No. Optional, never feeds back into scores. |

**Why the arithmetic stays in Python rather than Cypher.** The mandate's intent is "no LLM in the scoring path." A pure Python function meets that intent exactly. It runs at zero Aura compute cost, it is unit-tested without a database (`tests/test_scoring.py`), and it is versioned. Porting the formulas into Cypher is possible as one query, but testing then needs a live database and every score costs Aura Free CPU. **Open decision O1:** if the pitch requires "100% Cypher" literally, budget a port that keeps the Python version as a test oracle.

**The remaining source of non-determinism is ingestion.** The LLM currently returns free-floating `proficiency` values from 0.0 to 1.0, so two parses of the same resume can differ. Phase 2 removes that (§4.4): the LLM picks an enum label, and the Domain layer maps labels to fixed numbers.

---

## 3. Phase 1: Graph correctness (implemented)

### 3.1 Bug fixes G1–G5 (as built)

| Bug | Root cause | Fix (as built) | Where | Test |
|---|---|---|---|---|
| **G1** Duplicate edges | `MERGE (j)-[:REQUIRES_SKILL {importance, weight}]->(s)` matches on the property values, so a changed value creates a second edge. A skill listed in two tiers created two edges. | (a) `required_skill_rows` and `owned_skill_rows` reduce the extraction to one row per skill: tier precedence core > preferred > nice_to_have, highest proficiency wins. (b) Each relationship is MERGEd on the two nodes only, then properties are SET. (c) Replace semantics: before rewriting, delete the job's `REQUIRES_SKILL\|IN_DOMAIN` edges and the user's `HAS_SKILL {source:"resume"}` and `TARGETS` edges. (d) Everything runs in one `execute_write` transaction with `UNWIND`, which is atomic and automatically retried on transient errors. | `app/graph/writer.py`, `app/graph/normalize.py` | `test_*_merges_relationships_on_identity*`, `test_required_skill_rows_dedupes_*` |
| **G2** Domain bonus counted everything | Two independent `MATCH` clauses produced a cartesian product. | `(u)-[:TARGETS]->(d:Domain)<-[:IN_DOMAIN]-(j)` with `count(DISTINCT d)`. | `app/graph/reasoner.py` | `test_domain_bonus_requires_shared_domain_*` |
| **G3** A missing job scored 100% | Empty rows meant max = 0, so fit = 1.0. A missing user crashed the explanation step with a 500. | An existence check runs first and raises `NotFoundError`, which the API returns as 404. | `reasoner.py`, `pivot.py`, `routes.py` | `test_missing_*_raises_not_found`, `test_unknown_entities_return_404` |
| **G4** Prerequisites over-counted | `count(owned)` counted paths, not skills. | `count(DISTINCT owned)`. | `reasoner.py` | Same as G2. |
| **G5** LLM output overwrote the ontology | `SET s.category, s.level` ran on every ingestion, and the seed's property-map `MERGE` then hit a constraint violation. | `MERGE (s:Skill {name}) ON CREATE SET …` only. The job-specific level moved onto the relationship (`r.level`). The seed MERGEs on the key, then SETs (authoritative). | `writer.py`, `cypher/seed.cypher` | `test_skill_properties_only_set_on_create` |

**Cleanup for existing data:** `cypher/migrations/001_dedupe_edges.cypher` collapses duplicate edges written before the fix. It keeps the highest tier and highest proficiency, and is safe to re-run.

### 3.2 Remaining defects found in this review (closed by Phase 2)

| ID | Defect | Risk | Fix |
|---|---|---|---|
| R1 | `MERGE (d:Domain {name})` lets LLM output create new Domain nodes | Pollutes the graph | Resolve against the canonical domains; drop and record unknown ones (§4). |
| R2 | `MERGE (s:Skill {name})` on raw LLM strings ("pytorch", "PyTorch 2.x") | Pollutes the graph, wastes the node budget, and breaks prerequisite reasoning | Strict canonical resolver (§4). |
| R3 | `target_domain` can come back as the literal string `"null"` | Creates a junk Domain node | Enum in the LLM contract (§4.4). |
| R4 | Retry loop sleeps 10 s, then 20 s, while holding the semaphore | Exceeds Lambda budgets and wastes quota on non-transient errors | §6.4. |
| R5 | The Gemini client and settings are created when modules are imported | Cold-start cost; blocks MCP reuse | Lazy factories in Infrastructure (§7.1). |
| R6 | Pivot calls the LLM on every request to describe the target role | Breaks the determinism mandate (G7) and burns quota | Persist `(:Role)` profiles (§5.5). |
| R7 | The JD's required weight is treated as proof of the user's proficiency in pivot | Semantically wrong | Use only `HAS_SKILL`. A JD supplies context, not evidence. |

---

## 4. Canonical skill normalization

### 4.1 Goals

- **No graph pollution:** LLM output can never create a `Skill` or `Domain` node.
- **Space efficiency:** aliases cost **zero** graph nodes.
- **Deterministic:** the same input string always resolves to the same canonical skill for a given ontology version.
- **Curatable:** unknown terms are captured for review, never lost.

### 4.2 Ontology as code (source of truth)

`ontology/skills.yaml`, version-controlled and reviewed through pull requests:

```yaml
version: 3
domains:
  - {key: nlp, name: "NLP", description: "Natural language processing and LLMs"}
skills:
  - key: pytorch
    name: PyTorch
    category: ml_framework
    level: intermediate
    aliases: [torch, py-torch, "pytorch 2"]
    prerequisites: [{skill: numpy, strength: 0.9}]
    domains: [nlp]
```

- `cypher/seed.cypher` becomes **generated** from this file by `scripts/build_seed.py`, so it can't drift from the YAML.
- Because Aura Free deletes instances that stay inactive for 30 days, the whole ontology can be rebuilt from git in one command.
- Aliases stay in YAML and in process memory, never in the graph. On Aura Free this saves one node per alias compared with `(:SkillAlias)` nodes.

### 4.3 Canonical key function (Domain layer, pure)

```
canonical_key(s):
  1. Unicode NFKC normalize
  2. casefold
  3. replace [-_/] with a space
  4. drop every character not in [a-z0-9 +#.]   (keeps c++, c#, .net, node.js)
  5. collapse runs of whitespace; trim; strip a trailing "."
```

| Input | Key | Resolves via |
|---|---|---|
| `PyTorch`, ` pytorch ` | `pytorch` | key |
| `Py-Torch`, `torch` | `py torch`, `torch` | alias |
| `C++`, `c++` | `c++` | key |
| `Node.JS` | `node.js` | key |
| `Kubernetes (k8s)` | `kubernetes k8s` | unresolved, sent to the review queue |

**Resolution order:**
1. Exact key match.
2. Alias key match.
3. **Unresolved.** No fuzzy auto-merge, because "Java" vs. "JavaScript" is exactly the kind of mistake that can't be allowed. A fuzzy suggestion may be *recorded* for curators, never applied.

The resolver loads the YAML into a `dict[key, Skill]` once per Lambda container. That means zero graph reads and zero Redis commands.

### 4.4 Narrowing the LLM output contract

Use Gemini's JSON mode with a `response_schema` instead of stripping code fences:

| Field | Today | Phase 2 |
|---|---|---|
| `category` | free string | enum `language \| ml_framework \| platform \| concept \| tool` |
| `level` | free string | enum `foundational \| intermediate \| advanced` |
| `proficiency` | float 0–1 chosen by the LLM | **removed from the LLM contract** |
| `evidence` (new) | n/a | enum `mentioned \| coursework \| project \| professional \| led` |
| `importance` | implied by the list it appears in | explicit enum |
| `domains` / `target_domain` | free strings | enum built from `ontology.domains` |

The Domain layer maps labels to numbers with a fixed, versioned table:

```
RESUME_PROFICIENCY = {mentioned: 0.3, coursework: 0.5, project: 0.6, professional: 0.8, led: 0.95}
JD_REQUIRED_WEIGHT = {junior: 0.4, mid: 0.6, senior: 0.8, staff: 0.9}   # adjusted by level
```

This removes free-floating LLM numbers from the scoring inputs. Two parses that agree on the labels now produce identical scores.

**Hint, not constraint:** while the ontology has fewer than about 300 skills, the prompt includes the canonical names ("prefer these spellings"). This improves the hit rate without forcing an enum on `name`, which would silently drop new skills.

### 4.5 Strict mode: what happens to unresolved terms

Unresolved terms are **never** written as `Skill` nodes. Instead:

- They are stored as a bounded list property (at most 50) on the owning node: `j.unmapped_skills` or `u.unmapped_skills`. This costs no nodes and no relationships.
- `GET /admin/ontology/unmapped` aggregates them across the graph so curators can review them:
  ```cypher
  MATCH (n) WHERE n:User OR n:JobPosting
  UNWIND coalesce(n.unmapped_skills, []) AS term
  RETURN term, count(*) AS freq ORDER BY freq DESC LIMIT 100
  ```
- **Curation loop:** add an alias or skill to the YAML, re-seed, then `POST /admin/reresolve`. Re-resolution re-runs **only the resolver** against the cached extractions (§6.2), so it costs zero LLM calls.

### 4.6 Graph constraints and migration

```cypher
CREATE CONSTRAINT skill_key   IF NOT EXISTS FOR (s:Skill)  REQUIRE s.key  IS UNIQUE;
CREATE CONSTRAINT skill_name  IF NOT EXISTS FOR (s:Skill)  REQUIRE s.name IS UNIQUE;
CREATE CONSTRAINT domain_key  IF NOT EXISTS FOR (d:Domain) REQUIRE d.key  IS UNIQUE;
```

**Migration `002_canonicalize_skills`** is a Python script, chosen over relying on APOC being available on Free:
1. Load the YAML resolver. For every existing `Skill`, compute its resolution.
2. **Resolved to a different node:** move each `HAS_SKILL` / `REQUIRES_SKILL` edge to the canonical node, keeping the stronger edge on collision with the same tier and proficiency rules as `001`. Then `DETACH DELETE` the old node.
3. **Unresolved and not in the ontology:** append the name to each owner's `unmapped_skills`, then `DETACH DELETE` the node.
4. Run as one transaction per skill (resumable), with a dry run that prints the plan first.

---

## 5. Phase 2: Dynamic ingestion

### 5.1 Tenancy

The free tier has one Aura instance, so isolation happens at query level. That is acceptable for pilots and must be disclosed in the pitch.

- Every `User` and `JobPosting` carries `tenant_id` and a globally unique `uid = "{tenant_id}:{external_id}"`. The `uid` gets a single-property uniqueness constraint, which works on every edition. Composite constraints aren't needed.
- Every Cypher query in `domain/queries.py` takes `$tenant_id`. A test checks that every query string containing `:User` or `:JobPosting` references `tenant_id`.
- Auth: an `X-API-Key` header is hashed (SHA-256) and looked up in a small settings-backed key table, which maps to `tenant_id`. With Function URLs, this check lives in the app.
- `Skill`, `Domain` and `Role` nodes are **shared ontology**, with no tenant.

### 5.2 Endpoints (Interface layer)

All endpoints are under `/api/v2`. The `/api/v1` routes stay as aliases until the frontend migrates.

| Method & path | Use case | LLM? | Notes |
|---|---|---|---|
| `POST /parse/resume`, `POST /parse/jd` | Parse only (dry run) | Cached | Returns the extraction, resolved skills and unmapped terms. **No graph write.** |
| `PUT /users/{id}` | Upsert user metadata | No | |
| `PUT /users/{id}/skills` | Replace skills from structured input | No | Lets an LMS push skills it already has. No LLM cost. |
| `POST /users/{id}/resume` | Parse, resolve and write | Cached | Idempotent by content hash. |
| `GET /users/{id}` | Profile plus skills | No | |
| `PUT /jobs/{id}` | Replace job from structured input | No | |
| `POST /jobs/{id}/description` | Parse, resolve and write | Cached | Idempotent by content hash. |
| `GET /jobs/{id}`, `GET /jobs` | Read | No | Paginated. |
| `DELETE /users/{id}`, `DELETE /jobs/{id}` | `DETACH DELETE` | No | Needed for GDPR-style erasure and the relationship budget. |
| `GET /analyze/{user}/{job}` | Deterministic gap | No | Explanation removed from this call. |
| `POST /analyze/{user}/{job}/explanation` | Narrative | Yes | Budget-gated; the first thing to degrade. |
| `GET /skills/{key}/prerequisites` | Prerequisite chain | No | Future MCP tool `get_role_prerequisites`. |
| `GET /health`, `GET /health/graph` | Liveness and graph status | No | §7.4. |

The structured `PUT` endpoints are the main enterprise integration path. An LMS usually already holds structured skill data, so the LLM is only needed for unstructured documents. That fits a 250-RPD budget naturally.

### 5.3 Ingestion pipeline (Application layer)

```
text ──► normalize_text ──► h = sha256(prompt_ver | model | text)
          │
          ├─► node already has source_hash == h ? ──► 200 "unchanged" (0 LLM, 0 writes)
          ├─► cache.get(h) hit ? ──────────────────► extraction
          └─► limiter.acquire(tenant) ─► Gemini ─► validate ─► cache.set(h) ─► extraction
                     │ denied
                     └─► lexical fallback (§6.5) or 429 + Retry-After

extraction ──► resolver (Domain) ──► rows + unmapped
           ──► graph write (one tx: replace edges, set source_hash, unmapped_skills)
           ──► on graph outage: 503 GRAPH_UNAVAILABLE {extraction_cached: true}
```

**Ordering rule:** the extraction is cached *before* the graph write. If Aura is paused, the expensive LLM call is not lost, and the client's retry is free.

### 5.4 Write queries

These keep the G1/G5 guarantees and add resolution:

```cypher
// job write — $skills already resolved to canonical keys
MERGE (j:JobPosting {uid: $uid})
SET j.tenant_id = $tenant_id, j.title = $title, j.company = $company,
    j.seniority = $seniority, j.source_hash = $hash,
    j.unmapped_skills = $unmapped, j.extractor_version = $ver, j.updated_at = datetime()
WITH j
OPTIONAL MATCH (j)-[old:REQUIRES_SKILL|IN_DOMAIN]->()
DELETE old
WITH DISTINCT j
UNWIND $skills AS row
MATCH (s:Skill {key: row.key})              // MATCH, never MERGE: no node creation
MERGE (j)-[r:REQUIRES_SKILL]->(s)
SET r.importance = row.importance, r.weight = row.weight, r.level = row.level
```

The domain link uses `MATCH (d:Domain {key})`, again never `MERGE`.

### 5.5 Role profiles for pivot (fixes G7 / R6)

`(:Role {key, title, profile_version})-[:REQUIRES_SKILL {importance, weight, transferability}]->(:Skill)`

- A profile is generated **once** by the LLM (Application layer), resolved, then persisted. Curated roles can be committed to the YAML.
- `/pivot` then runs entirely in the Domain layer: Cypher fetches the user's skills and the role's skills, and the existing `score_pivot` rules compute the result. **No LLM call at request time**, so pivot becomes deterministic.
- `PIVOT_ROLES` in `Home.jsx` (8 roles) is about 8 nodes and roughly 150 relationships in total.

---

## 6. Gemini quota management

### 6.1 Principles

1. **Never call the LLM for the same input twice** (content-hash cache).
2. **Never call the LLM when structured input exists** (§5.2).
3. **Parsing outranks narration.** Explanations are the first casualty.
4. **Fail fast with Retry-After.** Never sleep inside a Lambda invocation.
5. **The quota is global per project**, then divided fairly among tenants.

### 6.2 Caching (two tiers, no Redis required locally)

| Tier | Key | Value | TTL | Cost |
|---|---|---|---|---|
| Graph marker | `source_hash` property on User/JobPosting | none (the presence check is enough) | life of the node | 0 extra |
| Extraction cache | `llm:x:{h}` | validated extraction JSON | 90 days | 1 GET, plus 1 SET on a miss |
| Role profile | `(:Role)` node | persisted profile | permanent | 0 |
| Explanation cache | `llm:e:{sha256(scoring_version, gap result)}` | text | 30 days | 1 GET + 1 SET |

- `h = sha256(prompt_version + model + normalize_text(text))`. Bumping `prompt_version` invalidates the cache deliberately.
- Cross-tenant cache hits are allowed for **extractions**, because identical text gives identical output and no tenant data leaks. Only the hash is shared. **Open decision O2:** disable this if a tenant contract forbids any shared processing.
- **Interface:** `Cache` protocol with `InMemoryLRU` (local, tests) and `UpstashCache` (deployed). It's selected by settings, so local development needs no Redis.

### 6.3 Rate limiter

A single atomic Redis Lua script costs **one command per acquire**:

```
keys:  gemini:rpm:{minute}            (TTL 60s)
       gemini:rpd:{YYYY-MM-DD PT}      (TTL 26h; Pacific date = Google's reset)
       tenant:{t}:rpd:{date}           (TTL 26h)
       gemini:cooldown                 (set on 429, TTL = retry-after)
args:  rpm_limit, rpd_limit, tenant_share, reserve_for_parse, kind (parse|explain)

if cooldown exists                        → deny(retry_after = ttl(cooldown))
if rpm >= rpm_limit                       → deny(retry_after = seconds to next minute)
if kind == explain and rpd >= rpd_limit * (1 - reserve_for_parse) → deny
if rpd >= rpd_limit                       → deny(retry_after = seconds to PT midnight)
if tenant_rpd >= tenant_share             → deny
INCR all three; allow
```

- Reserve **before** the call and don't refund on failure. That is conservative: the count never exceeds Google's.
- Defaults (`settings`): `GEMINI_RPM=8`, `GEMINI_RPD=200`. Both sit below the most conservative published numbers. Set them from the AI Studio console. `reserve_for_parse=0.8`, `tenant_share=rpd_limit / active_tenants` with a floor of 10.
- Local and test: an `InMemoryLimiter` with the same interface. The current per-process `asyncio.Semaphore(8)` is kept only as an in-container concurrency guard.

**Upstash budget check:** one LLM-bound request costs at most 3 commands (cache GET, limiter EVAL, cache SET). 500k/month allows about 5,500 LLM-bound requests per day, far above the Gemini quota, so Redis is never the bottleneck. Pure graph reads cost zero Redis commands.

### 6.4 Handling Gemini errors

| Error | Classification | Action |
|---|---|---|
| `429 RESOURCE_EXHAUSTED` | Quota | Read the retry delay if present (default 60 s). Set `gemini:cooldown`. Return **HTTP 429** with `Retry-After` and code `LLM_QUOTA`. **No in-request retry.** |
| 500 / 503 / timeout | Transient | **One** retry after 1–2 s with jitter, then 503 `LLM_UNAVAILABLE`. |
| JSON or schema validation failure | Model output | One retry with a "return valid JSON only" nudge (counts against quota), then 502 `LLM_BAD_OUTPUT`. |
| 400 / 401 / 403 | Configuration | No retry. 500 `LLM_CONFIG`, logged loudly. |

This replaces the current 10 s / 20 s sleeps (R4). Every LLM call gets a hard client timeout (20 s), well inside the Lambda timeout.

### 6.5 Lexical fallback when the quota is spent

When the limiter denies a parse, the Application layer can fall back to a **deterministic** extractor. It scans the normalized text for every canonical key and alias, using word-boundary regex built from the YAML once per container.

- Output carries `extraction_mode: "lexical"`. Proficiency and importance get the conservative defaults `evidence: mentioned` and `importance: preferred`.
- Quality is lower but it costs nothing and is perfectly reproducible, which is useful demo material for the deterministic story.
- The result is **not** cached under the LLM hash, so a later LLM parse replaces it. The tenant can choose `fallback=lexical|reject` per request.

---

## 7. Neo4j Aura Free resilience

### 7.1 Driver lifecycle on Lambda

- Create the driver **lazily** on first use and keep it at module scope, so warm invocations reuse the pool. Don't connect when the module is imported (R5).
- Driver settings (`infrastructure/graph/driver.py`):

| Setting | Value | Why |
|---|---|---|
| `max_connection_pool_size` | 5 | One request per invocation. Keeps Aura connection counts low. |
| `connection_timeout` | 5 s | Fail fast when the instance is paused. Don't hang until Lambda times out. |
| `connection_acquisition_timeout` | 10 s | |
| `max_transaction_retry_time` | 8 s | Bounds `execute_read`/`execute_write` automatic retries. |
| `liveness_check_timeout` | 30 s | **Critical on Lambda.** Frozen containers hold dead sockets, so pooled connections idle for 30 s or more are checked before use. |
| `max_connection_lifetime` | 300 s | Recycles before cloud load balancers drop idle connections. |

- All reads and writes go through `execute_read` / `execute_write` (already true after Phase 1), so the driver retries transient failures and leader switches automatically.

### 7.2 Error taxonomy

| Driver error | Meaning | API result |
|---|---|---|
| `ServiceUnavailable` at connect, `DNS`/`OSError` | Instance paused, deleted, or network down | 503 `GRAPH_UNAVAILABLE` + admin hint |
| `SessionExpired`, `TransientError` | Transient | Retried by the managed transaction; 503 if the retries are exhausted |
| `AuthError` | Credentials | 500 `GRAPH_CONFIG` (alert) |
| `ConstraintError` | Data conflict | 409 |
| `ClientError` (Cypher) | Bug | 500, logged with the query name, not its parameters |

A connection error can't distinguish "paused" from "network down," so the hint names both. The heartbeat log (§7.5) tells the admin which it was.

### 7.3 Circuit breaker (per container, in memory, 0 cost)

- **Closed → open** after 3 `GRAPH_UNAVAILABLE` results within 60 s.
- **Open:** for 30 s, reject immediately with 503 and no connection attempt. This saves Lambda billed time and gives clients a quick, clear error.
- **Half-open:** one probe (`verify_connectivity`, 3 s timeout). Success closes the breaker.

### 7.4 Telling the admin to resume the instance

- `GET /health` is liveness only and never touches the graph.
- `GET /health/graph` returns:
  ```json
  {"graph": "unavailable", "breaker": "open", "since": "2026-10-15T09:12:00Z",
   "hint": "Aura Free may be paused after ~3 days of inactivity. Resume it at console.neo4j.io → Instances → Resume. Instances inactive for 30 days are deleted."}
  ```
- Every 503 `GRAPH_UNAVAILABLE` body carries the same `hint`. The frontend shows it as a banner. No secrets or URIs appear in the body.
- **Optional:** the heartbeat job (§7.5) emails the admin on its first failure. Use a free email path, for example the SNS email tier, after verifying its current terms.

### 7.5 Heartbeat to stay active

- An EventBridge Scheduler rule invokes the Lambda once every 24 h with `{"task":"heartbeat"}`. Verify EventBridge Scheduler's current free allowance; one call a day is negligible either way.
- It runs `MERGE (h:Heartbeat {id:'nextnode'}) SET h.at = datetime()`. That is one node, and a **write**, because it's unconfirmed whether reads reset Aura's inactivity timer.
- This prevents both the 3-day pause and the 30-day deletion. **Open decision O3:** confirm this complies with Aura Free's terms of use before relying on it.
- Weekly, the same job exports tenant data (`User`/`JobPosting` with their edges) as JSON to an always-free store. Aura Free has no backups.

---

## 8. Capacity plan for Aura Free

Based on the conservative official limit of 50k nodes / 175k relationships:

| Item | Nodes | Relationships |
|---|---|---|
| Ontology: 3,000 skills, 20 domains | 3,020 | ~6,000 prerequisite edges + ~3,000 `BELONGS_TO` |
| Roles (50 curated) | 50 | ~1,250 |
| Heartbeat | 1 | 0 |
| **Remaining for tenants** | ~46,900 | **~164,700** |
| One user (≈25 skills + 1 target) | 1 | ~26 |
| One job (≈20 skills + 2 domains) | 1 | ~22 |

**Relationships are the binding limit.** About 164,700 ÷ 24 ≈ **6,800 users plus jobs** in total, while nodes would allow about 46,000. Consequences:

- **Analyses are computed on demand and never persisted.** That's zero growth per analysis.
- Aliases are kept out of the graph (§4.2). Graph-stored aliases would cost one relationship each.
- Retention: jobs not analysed for 90 days are archived to the JSON export and deleted. `DELETE /jobs/{id}` is exposed for tenants.
- Capacity is shown on `GET /health/graph` as `{nodes, relationships, pct_of_limit}`, with a warning above 80%.

---

## 9. Phase 2 delivery plan

Each step merges independently. "Done" means the listed tests pass and the existing 18 remain green.

| Step | Deliverable | Acceptance tests |
|---|---|---|
| 2.1 | Layer skeleton (`interface/ application/ domain/ infrastructure/`) with shims | All existing tests pass unchanged. An import-lint test enforces the dependency rule. |
| 2.2 | `ontology/skills.yaml`, `canonical_key`, resolver, generated `seed.cypher` | Golden table from §4.3. Seed generation is byte-stable. |
| 2.3 | LLM contract v2 (enums, JSON mode, no float proficiency) plus label→number tables | Contract tests use recorded Gemini responses, no network. |
| 2.4 | `Cache` and `RateLimiter` protocols with in-memory implementations; Gemini error taxonomy; remove the sleeps | Limiter tests for RPM, RPD, the explain reserve, tenant share and the Pacific-midnight boundary (injected clock). 429 → `Retry-After`. |
| 2.5 | Ingestion pipeline (§5.3), hash idempotency, strict writes (`MATCH` skills) | Resubmitting identical text causes 0 LLM calls (spy) and 0 writes. Unknown skill → `unmapped_skills`, no `Skill` node created. |
| 2.6 | Tenancy (`uid`, `tenant_id`, API keys) and v2 endpoints | Tenant A can't read tenant B (404). Every query string references `tenant_id`. |
| 2.7 | Driver hardening, circuit breaker, `/health/graph` | Fake driver raising `ServiceUnavailable` → breaker opens → 503 with hint. No connection attempt while open. |
| 2.8 | Migration `002_canonicalize_skills` (dry run, then apply) | Run against a local Neo4j container: duplicates merged, no orphaned edges. |
| 2.9 | Role profiles plus deterministic pivot | Pivot makes 0 LLM calls once the role exists. Same input gives an identical output. |
| 2.10 | Lexical fallback | Same text gives identical output. `extraction_mode` is set. |
| 2.11 | Frontend switched to the v2 API (real calls, per-browser user id, safe markdown) | Manual end-to-end run against a local stack. |

Steps 2.1–2.7 need no Redis, no AWS and no live LLM. Upstash, the Lambda Function URL, EventBridge and the Amplify (or alternative) deployment follow in Phase 5.

## 10. Open decisions

| ID | Decision | Default if not decided |
|---|---|---|
| O1 | Literal "all arithmetic in Cypher", or Cypher traversal plus pure Python rules | Keep Python rules; report `scoring_version` |
| O2 | Allow sharing the extraction cache across tenants by content hash | Allowed |
| O3 | Use a heartbeat to keep Aura Free active (check terms of use) | Enabled, with a manual-resume runbook as fallback |
| O4 | Where real tenant PII can be processed, given that the free tier may train on it | Demo data only on the $0 tier |
| O5 | Frontend host (Amplify's free term is unverified) | Static host with a permanent free tier |
