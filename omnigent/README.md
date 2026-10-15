# NextNode × Omnigent (local orchestrator)

Multi-agent curriculum workflow run by [Omnigent](https://github.com/omnigent-ai/omnigent)
(v0.18.0, alpha) on your machine. Design: [`docs/TDD-phase3-omnigent.md`](../docs/TDD-phase3-omnigent.md).

```
omnigent/
├── server_config.yaml          server-wide guardrails (budget, loop guard, tool cap)
├── policies/
│   └── nextnode_policies.py    custom policies: daily_llm_budget, dispatch_budget, deny_tools
└── nextnode_curriculum/
    ├── config.yaml             orchestrator (Gemini, no NextNode tools)
    └── agents/
        ├── parser/config.yaml    raw text → JSON (local Ollama model, no tools)
        ├── reasoner/config.yaml  NextNode MCP read + write tools (Gemini)
        └── reviewer/config.yaml  NextNode MCP read-only tools (local model)
```

> **Prerequisite:** `app/mcp_server.py` (the NextNode MCP server, TDD §3) is not built
> yet. The startup scripts check for it and stop with a clear message.

## One-time setup

1. **Omnigent** (Python 3.12+; Windows runs in reduced mode without tmux/sandboxing,
   which this workflow does not use):
   ```bash
   uv tool install omnigent
   ```
2. **Ollama** with the local model used by the Parser and Reviewer:
   ```bash
   ollama pull qwen3:8b
   ```
3. **Two gateway credentials**, added interactively so no key lands in this repo:
   ```bash
   omnigent setup
   ```
   | Name | Type | Base URL | Key |
   |---|---|---|---|
   | `gemini-free` | Gateway | `https://generativelanguage.googleapis.com/v1beta/openai/` | a Gemini API key, ideally from a **separate** Google Cloud project than the NextNode API |
   | `ollama-local` | Gateway | `http://localhost:11434/v1` | any value (Ollama ignores it) |

   Configure **no paid providers**. With only these two, no agent can reach a paid
   model, which is what guarantees the $0 budget. The policies only cap free-tier usage.

## Run

Windows:
```bash
.\scripts\omnigent_up.ps1 -Run -UserId u1 -JobId j1 -ResumeFile cv.txt -JobFile jd.txt
```
macOS / Linux:
```bash
scripts/omnigent_up.sh run u1 j1 cv.txt jd.txt
```

Each script:
1. checks Omnigent, Ollama, the local model and the MCP server module;
2. generates a per-run `NEXTNODE_MCP_TOKEN` shared secret, kept in memory only;
3. starts the NextNode MCP server on `127.0.0.1:8765`;
4. starts `omnigent server --config omnigent/server_config.yaml` with `omnigent/policies`
   on `PYTHONPATH`;
5. runs `omnigent run omnigent/nextnode_curriculum/ -p "<ids + texts>"`.

Logs go to `.omnigent-logs/`.

## Guardrails

| Policy | Scope | Effect |
|---|---|---|
| `gemini_daily_budget` | every session | DENY Gemini calls after 150 requests or 1M tokens per Pacific day, counted across all sessions |
| `tool_call_cap` | every session | DENY after 40 tool calls in one session |
| `loop_guard` | every session | ASK when the same tool call repeats 3× in 10 |
| `dispatch_budget` | orchestrator | DENY after 6 sub-agent runs in a session (one revision max) |
| `spawn_bounds` | orchestrator | at most 2 dispatches per turn |
| `read_only` | reviewer | DENY any NextNode write tool |

Today's Gemini usage is in `~/.nextnode/omnigent_budget_gemini.json`.
