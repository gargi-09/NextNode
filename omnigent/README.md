# NextNode × Omnigent (local orchestrator)

Multi-agent curriculum workflow run by [Omnigent](https://github.com/omnigent-ai/omnigent)
(v0.19.1, alpha) on your machine. Design: [`docs/TDD-phase3-omnigent.md`](../docs/TDD-phase3-omnigent.md);
self-hosted models: [`docs/TDD-phase4-vllm.md`](../docs/TDD-phase4-vllm.md).

```
omnigent/
├── server_config.yaml          server-wide guardrails (budget, loop guard, tool cap)
├── policies/
│   └── nextnode_policies.py    custom policies: daily_llm_budget, dispatch_budget, deny_tools
└── nextnode_curriculum/
    ├── config.yaml             orchestrator (vLLM qwen3-4b, no NextNode tools)
    └── agents/
        ├── parser/config.yaml    raw text → JSON (vLLM qwen3-4b, no tools)
        ├── reasoner/config.yaml  NextNode MCP read + write tools (vLLM qwen3-4b)
        └── reviewer/config.yaml  NextNode MCP read-only tools (Ollama llama3.1:8b)
```

The agents' graph tools come from the NextNode MCP server, `app/mcp_server.py` (TDD §3).
The startup scripts run it for you; to try it alone:
`python -m app.mcp_server --transport http --port 8765` with `NEXTNODE_MCP_TOKEN` set.

## One-time setup

1. **Omnigent** (Python 3.12+; Windows runs in reduced mode without tmux/sandboxing,
   which this workflow does not use):
   ```bash
   uv tool install omnigent==0.19.1
   ```
2. **vLLM on your GPU cluster** (Orchestrator, Parser, Reasoner). Deploy and open the tunnel:
   ```bash
   .\scripts\vllm_tunnel.ps1 -Deploy
   ```
   (`scripts/vllm_tunnel.sh deploy` on macOS/Linux). Details: `docs/TDD-phase4-vllm.md`.
   **No GPU yet?** `.\scripts\vllm_standin.ps1` serves the same model (Qwen3-4B, on Ollama) at the
   same address, so everything below works unchanged; the `vllm-gpu` key can be any value until you
   switch to the real tunnel. Check either with `venv\Scripts\python scripts\vllm_smoke.py --mcp`.
3. **Ollama** with the Reviewer's model (a different family from the Reasoner's Qwen):
   ```bash
   ollama pull llama3.1:8b
   ```
4. **Two model providers** in `~/.omnigent/config.yaml`: copy the entries from
   [`providers.example.yaml`](providers.example.yaml) under its `providers:` key. (Omnigent 0.19's
   `omnigent setup` only offers custom gateways inside a harness CLI's own flow, and the
   `openai-agents` harness these agents use has none.) Neither entry holds a secret: `vllm-gpu`
   reads its key from Omnigent's secret store (Windows Credential Manager), which `omnigent_up`
   fills on each run: from the cluster Secret with `-KeyFromCluster`, or a placeholder for the
   Ollama stand-in. (An `env:` reference would not work: Omnigent filters its runners' environment.)
   Check with `omnigent config list`.

   Configure **no paid providers**. With only these two, no agent can reach a paid
   model, which is what guarantees the $0 budget (GPU hours come from cloud credits;
   scale the Deployment to 0 when idle).

## Run

**On a 4B model, use the scripted runner.** It runs the same agents (prompts, models, providers
and tool allow-lists are read from this bundle) in a fixed order: parse, reason, review, at
most one revision. The Omnigent orchestrator below needs a stronger model: on Qwen3-4B it
dispatches the parsers and then stalls ("I will now wait for the results") instead of
calling the Reasoner. With the model server open (tunnel or stand-in):
```bash
venv\Scripts\python scripts\curriculum_run.py --user-id demo-user --job-id demo-job --resume omnigent\samples\resume.txt --job omnigent\samples\jd.txt
```
The verdict comes from the reviewer's seven checks done in code against the graph; the
Llama 3.1 reviewer's findings are advisory (on CPU it flagged every check, even on a
curriculum the graph confirmed correct). Add `--key-from-cluster` against the real vLLM. The result is printed and saved to
`.omnigent-logs/curriculum-<user>-<job>.json`.

**Omnigent orchestrator** (for a stronger model). Keep the tunnel open in its own terminal
(`.\scripts\vllm_tunnel.ps1`), then:

Windows:
```bash
.\scripts\omnigent_up.ps1 -Run -UserId u1 -JobId j1 -ResumeFile cv.txt -JobFile jd.txt
```
macOS / Linux:
```bash
scripts/omnigent_up.sh run u1 j1 cv.txt jd.txt
```

Each script:
1. checks Omnigent, the vLLM tunnel, Ollama, the Reviewer's model and the MCP server module;
2. generates a per-run `NEXTNODE_MCP_TOKEN` shared secret, kept in memory only;
3. starts the NextNode MCP server on `127.0.0.1:8765`;
4. starts `omnigent server --config omnigent/server_config.yaml` with `omnigent/policies`
   on `PYTHONPATH`;
5. runs `omnigent run omnigent/nextnode_curriculum/ -p "<ids + texts>"`.

Logs go to `.omnigent-logs/`.

## Guardrails

| Policy | Scope | Effect |
|---|---|---|
| `gemini_daily_budget` | every session | DENY Gemini calls after 150 requests or 1M tokens per Pacific day (inactive since Phase 4: no agent uses Gemini) |
| `tool_call_cap` | every session | DENY after 40 tool calls in one session |
| `loop_guard` | every session | ASK when the same tool call repeats 3× in 10 |
| `dispatch_budget` | orchestrator | DENY after 6 sub-agent runs in a session (one revision max) |
| `spawn_bounds` | orchestrator | at most 2 dispatches per turn |
| `read_only` | reviewer | DENY any NextNode write tool |

Today's Gemini usage is in `~/.nextnode/omnigent_budget_gemini.json`.
