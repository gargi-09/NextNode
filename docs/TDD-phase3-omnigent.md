# NextNode — Technical Design Document: Phase 3 (Omnigent Orchestration Layer)

| | |
|---|---|
| Status | Proposed. Configs and policies written; MCP server (§3) built and verified against Aura (2026-10-16); end-to-end run not yet validated |
| Scope | `omnigent/` directory, NextNode MCP tool contract, guardrails, local startup |
| Target | Omnigent **v0.18.0** (released 2026-10-13, Apache-2.0, alpha), checked against its source |
| Last reviewed | 2026-10-15 |

---

## 0. Corrections to the brief

| # | Brief assumed | Actual state | Consequence |
|---|---|---|---|
| C1 | "The FastMCP server now exposes `calculate_skill_gap`, `upsert_user_skills`" | **No MCP server exists in the repo.** Phase 2 built REST endpoints and services only. | Building the MCP server is step P3.0. §3 defines its exact tool contract; the YAML is written against it. |
| C2 | Cap API spend with Omnigent's budget policy | `cost_budget` caps **USD**. Free-tier Gemini and local Ollama models are unpriced (`total_cost_usd` = 0), so the cap can never fire; on unpriced models it only asks once to "continue untracked". | A custom `daily_llm_budget` policy counts **requests and tokens per Pacific day**, which is what the free tier actually limits. The real $0 guarantee is configuring no paid provider (§6.1). |
| C3 | Policy state lives in the session | `session_state` is **per session**, and every sub-agent runs as its own session. The Gemini quota is per project per day. | The budget ledger is a small local file shared by all sessions (§6.2). |
| C4 | "Exact" Omnigent YAML | Omnigent is alpha, and two policy syntaxes coexist (`handler`/`factory_params` and `function: {path, arguments}`). | The configs use the form documented in `docs/POLICIES.md` and `AGENT_YAML_SPEC.md` at v0.18.0. Pin the Omnigent version (§8). |

---

## 1. Architecture

```
            ┌──────────────── local machine ($0) ────────────────────────────┐
  user ──►  │ omnigent server  (server_config.yaml: budget, loop, tool caps)  │
            │   └─ nextnode_curriculum   orchestrator  · Gemini · no tools    │
            │        ├─ parser    local qwen3:8b · no tools                   │
            │        ├─ reasoner  Gemini · NextNode MCP read+write ──┐        │
            │        └─ reviewer  local qwen3:8b · MCP read-only ────┤        │
            │                                                        ▼        │
            │ NextNode MCP server  127.0.0.1:8765/mcp  (stateless, bearer)    │
            │   └─ app/services/*  →  Neo4j Aura Free  (deterministic graph)  │
            └─────────────────────────────────────────────────────────────────┘
```

**Separation of concerns**
- **NextNode** stays a stateless tool server. It holds no conversation state, makes **no LLM calls** through MCP, and every tool maps to an existing Phase 2 service.
- **Omnigent** owns orchestration, sessions, model routing, and guardrails.
- **Parsing moves out of NextNode.** The Parser agent produces structured JSON, and the Reasoner writes it through structured upsert tools. The NextNode API's own Gemini budget is never touched by the orchestrator. Strict ontology resolution still runs server-side, so LLM output still can't create `Skill` nodes.

**Cross-vendor review**

| Agent | Model (vendor) | Why |
|---|---|---|
| Orchestrator | `gemini-2.5-flash` (Google) | Needs reliable tool use for `sys_session_send` routing |
| Parser | `qwen3:8b` (Alibaba, local) | Extraction is easy; spends no free-tier quota |
| Reasoner | `gemini-2.5-flash` (Google) | Multi-step MCP tool use |
| Reviewer | `qwen3:8b` (Alibaba, local) | **Different vendor from the Reasoner**, so the two can't share blind spots; costs no quota |

All four use the `openai-agents` harness with two gateway credentials: Gemini's OpenAI-compatible endpoint and Ollama. One harness keeps the surface small (§7, risk R1).

---

## 2. Directory structure

```
omnigent/
├── README.md                       runbook
├── server_config.yaml              server-wide policies + policy_modules
├── policies/
│   └── nextnode_policies.py        daily_llm_budget, dispatch_budget, deny_tools (dependency-free)
└── nextnode_curriculum/            one Omnigent agent bundle
    ├── config.yaml                 orchestrator
    └── agents/
        ├── parser/config.yaml
        ├── reasoner/config.yaml
        └── reviewer/config.yaml
scripts/omnigent_up.ps1 / .sh       start MCP server + Omnigent server, optional run
tests/test_omnigent_config.py       policy unit tests + config consistency checks
app/mcp_server.py                   NextNode MCP server (P3.0, built)
```

**Naming hazard avoided.** The policy module is imported as `nextnode_policies`, with `omnigent/policies` on `PYTHONPATH`, never as `omnigent.policies…`. Putting the repo root on Omnigent's path would make this repo's `omnigent/` folder compete with the real `omnigent` package.

---

## 3. NextNode MCP tool contract (P3.0 — built)

**Transport and security**
- MCP Streamable HTTP on `127.0.0.1:8765/mcp`, bound to loopback only.
- Requires `Authorization: Bearer $NEXTNODE_MCP_TOKEN`. The startup script generates a per-run token and keeps it in memory only.
- `GET /health` for the startup probe.

**Implementation:** `app/mcp_server.py`, using the official `mcp` Python SDK (FastMCP, pinned 1.30.0), stateless Streamable HTTP with JSON responses, Host/Origin checks against DNS rebinding, and refusal to bind a non-loopback host or start without a 16+ character token. Each tool is a thin wrapper over `app/services/*`, so tool behavior is exactly the REST behavior: strict ontology resolution, single-transaction writes, circuit breaker, 404/409/503 semantics.

| Tool | R/W | Wraps | Input | Output |
|---|---|---|---|---|
| `resolve_skills` | R | `ontology.resolve` | `names: list[str]` | `{resolved: [{input, canonical}], unmapped: [str]}` |
| `get_user_profile` | R | `users.get_user` | `user_id` | user + skills + `unmapped_skills` |
| `get_job_posting` | R | `jobs.get_job` | `job_id` | job + required skills + domains |
| `calculate_skill_gap` | R | `analysis.analyze_gap(explain=False)` | `user_id, job_id` | `AnalysisResponse` **without** `graph` / `roadmap_flow`, to save context tokens |
| `get_skill_prerequisites` | R | new read: `PREREQUISITE_OF*1..3` into a skill | `skill` | `[{skill, depth, strength}]` |
| `upsert_user_skills` | W | create-if-missing + `users.replace_skills` | `user_id, name?, skills: [{name, proficiency}]` | `{skills_written, unmapped_skills}` |
| `upsert_job_posting` | W | `writer.write_job` (upsert) | `job_id, title, company?, seniority?, core/preferred/nice_to_have_skills: [{name, weight}], domains` | `{skills_written, unmapped_skills}` |

**Errors** are returned as MCP tool errors whose text is JSON with NextNode's machine codes: `NOT_FOUND`, `CONFLICT`, `UNPROCESSABLE`, `GRAPH_UNAVAILABLE` (with `retry_after` and hint) or `INTERNAL` (details stay in the server log). Schema violations (e.g. proficiency > 1) are rejected by the SDK before the tool runs, as a plain validation message. `get_skill_prerequisites` returns `{skill, prerequisites: [{skill, depth, strength}]}`, where strength is the product of edge strengths along the path. Agent prompts treat any tool error as terminal for that step.

**Deliberately not exposed:** delete tools, and the LLM-backed `/extract`, `/pivot` and explanation paths. The orchestrator must never spend the API's Gemini budget, and an agent must never delete data.

---

## 4. Workflow

```
1  parse      orchestrator ─► parser  (MODE: RESUME)  ┐ same turn
              orchestrator ─► parser  (MODE: JOB)     ┘
2  reason     orchestrator ─► reasoner  upsert_user_skills, upsert_job_posting,
                                        calculate_skill_gap, get_skill_prerequisites ×≤6
3  review     orchestrator ─► reviewer  calculate_skill_gap, get_skill_prerequisites,
                                        get_user_profile → PASS | REVISE
4  revise     (only if REVISE, at most once) reasoner fixes the blocking issues → reviewer again
5  finish     curriculum JSON + verdict + open issues + unmapped_skills
```

**Data contracts** (full schemas are in the agent prompts):
- **Parser → Reasoner:** `{"kind": "resume", "skills": [{"name", "evidence", "proficiency"}], ...}` / `{"kind": "job", "core_skills": [{"name", "weight"}], ...}`. Proficiency comes from a fixed evidence table (`mentioned` 0.3 … `led` 0.95), which is TDD-phase1-2 §4.4 applied at the agent layer.
- **Reasoner → Reviewer:** curriculum `{fit_score, scoring_version, modules[{order, skill, priority, why, builds_on, learn_first, activities, estimated_hours, evidence_of_mastery}], unmapped_skills}`. `fit_score`, `scoring_version`, `priority` and `why` are **copied** from `calculate_skill_gap`, so every number in the output comes from the deterministic Domain layer.
- **Reviewer:** `{verdict, blocking[{check, module, issue, evidence}], non_blocking[...]}`. Seven checks, each verified with a tool call, never by trusting the draft.

**Where determinism holds:** graph writes, scores, roadmap order and prerequisite facts all come from NextNode. Only the module *activities* text is generative, and the Reviewer constrains it (no fabricated URLs or brands; hours within 1–200).

---

## 5. Guardrails

| Policy | Where | Builtin / custom | Stops |
|---|---|---|---|
| `gemini_daily_budget` | server-wide | custom `daily_llm_budget` | free-tier overrun: 150 requests / 1M tokens per Pacific day, all sessions |
| `tool_call_cap` | server-wide | builtin `max_tool_calls_per_session(40)` | runaway tool use in any agent |
| `loop_guard` | server-wide | builtin `detect_loop(10, 3)` → ASK | the same failing call retried |
| `dispatch_budget` | orchestrator | custom, limit 6 | Reasoner ↔ Reviewer ping-pong; makes "one revision" a hard limit, not just a prompt rule |
| `spawn_bounds` | orchestrator | builtin, 2 per turn | fan-out bursts |
| `read_only` | reviewer | custom `deny_tools` | reviewer writing to the graph, even if its MCP allow-list grows |

Server-wide policies apply to every session, including each sub-agent's own session, so no agent spec can opt out of them.

---

## 6. $0 budget design

### 6.1 The hard guarantee is provider configuration, not policy
Only two gateway credentials exist in `omnigent setup`: `gemini-free` (a free-tier key) and `ollama-local`. No agent can reach a paid model because none is configured. **Never** add an Anthropic or OpenAI key or a subscription to this Omnigent install. If one is ever needed, add Omnigent's `cost_budget` with a small `max_cost_usd` as a tripwire.

### 6.2 `daily_llm_budget` mechanics
- **What it counts:** fires on `llm_request`, one per model call. Harnesses that emit only the per-turn `request` phase are counted per turn.
- **Tokens:** the cumulative session `usage.total_tokens` is turned into a per-session delta through `session_state`, then added to a shared ledger at `~/.nextnode/omnigent_budget_<ledger>.json`.
- **Day boundary:** Pacific date, matching Google's RPD reset.
- **Concurrency:** writes are atomic (`os.replace`) under a process lock. That's sufficient for one local Omnigent server; it is *not* safe across machines, which needs Redis, as in TDD-phase1-2 §6.3.
- **Model filter:** only counts models matching `model_substrings: [gemini]`, so local Ollama calls are free and unlimited apart from the loop and tool caps.

### 6.3 Expected usage per run

| Agent | Gemini calls per run (estimate) |
|---|---|
| Orchestrator | 5–8 turns |
| Reasoner | 8–12 (2 upserts, 1 gap, up to 6 prereqs, final draft) |
| Revision (if any) | +4–6 |
| Parser, Reviewer | 0 (local) |

That's roughly **15–25 Gemini requests per run, so 150/day ≈ 6–10 runs**. Validate this on the first runs: the ledger file shows real counts. Moving the orchestrator to a local model would roughly halve Gemini use, at some risk to routing reliability.

---

## 7. Risks and open questions

| ID | Risk | Mitigation / how to verify |
|---|---|---|
| R1 | `openai-agents` + Gemini's OpenAI-compatible endpoint is not a combination Omnigent documents (its examples use Databricks, OpenRouter and Ollama gateways). If the harness needs an API Gemini doesn't implement, Gemini agents won't start. | First validation step: `omnigent run omnigent/nextnode_curriculum/agents/reasoner -p "Say hello"`. Fallback: `harness: antigravity` with `auth: {type: api_key, api_key: ${GEMINI_API_KEY}}`, which is Gemini-native and documented. |
| R2 | `qwen3:8b` may be too weak a reviewer for 7 tool-verified checks | Each check is a tool call plus comparison. If quality is poor, use a larger local model (`qwen3:14b`) and keep the vendor different from the Reasoner. |
| R3 | `llm_request` phase emission varies by harness | The policy falls back to per-turn counting; confirm with the ledger after one run. |
| R4 | Omnigent is alpha, and its spec changes between releases | Pin the version (`uv tool install omnigent==0.18.0`). Re-validate on upgrade; `tests/test_omnigent_config.py` checks the configs' internal consistency. |
| R5 | `tools:` allow-list support on URL-based MCP servers is only documented for the stdio form | Belt and braces: `deny_tools` on the reviewer blocks write tools regardless. |
| R6 | Windows runs Omnigent in reduced mode | The workflow uses no terminals, `os_env` or sandboxes, so nothing it needs is disabled. |
| O1 | Should the orchestrator run locally, halving Gemini use? | Decide after measuring R3 and §6.3. |

## 8. Delivery plan

| Step | Deliverable | Done when |
|---|---|---|
| P3.0 ✅ | `app/mcp_server.py` per §3, plus the `mcp` dependency | Tool tests pass with fake services; `/health` responds; bearer auth enforced (`tests/test_mcp_server.py`; live run against Aura matched the REST fit score) |
| P3.1 | This directory (done) | `pytest tests/test_omnigent_config.py` passes |
| P3.2 | Validate R1 with a single-agent hello run per agent | Each of the 4 agents answers |
| P3.3 | First end-to-end run on test data (`u_test` / `j_test`) | Curriculum returned; reviewer PASS or a justified REVISE; ledger shows request counts |
| P3.4 | Tune budgets and models from the measured usage | `max_requests_per_day` set from real numbers |
