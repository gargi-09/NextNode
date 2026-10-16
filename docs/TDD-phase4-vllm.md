# TDD — Phase 4: Self-hosted inference with vLLM

Status: manifests, agent configs and tunnel scripts written and statically tested; not yet
deployed. Builds on [TDD-phase3-omnigent.md](TDD-phase3-omnigent.md).

## 1. Goal
Remove the Gemini rate limit from the multi-agent workflow at $0 cash cost: run an
open-weight model on a GPU cluster paid for with cloud credits, behind vLLM's
OpenAI-compatible API, and point the Omnigent agents at it.

The NextNode **API** (`app/extraction/llm.py`) still uses Gemini. Switching it is a
separate change (§7).

## 2. Topology

```
 your machine                                         GPU cluster (namespace nextnode-inference)
┌──────────────────────────────────────┐            ┌────────────────────────────────────────┐
│ Omnigent server + agents             │            │ Service vllm (ClusterIP :8000)         │
│  orchestrator ─┐                     │  kubectl   │   └─ Deployment vllm (1 pod, 1 GPU)     │
│  parser ───────┼─► localhost:8000/v1 ├─port-fwd──►│       vllm/vllm-openai:v0.11.0          │
│  reasoner ─────┘   (gateway vllm-gpu)│  (loopback)│       Qwen3-4B-Instruct-2507 = qwen3-4b │
│     │ MCP tool calls                 │            │       PVC vllm-model-cache (HF weights) │
│     ▼                                │            └────────────────────────────────────────┘
│ NextNode MCP server 127.0.0.1:8765 ──┼──► Neo4j Aura (deterministic Cypher)
│ reviewer ──► Ollama llama3.1:8b      │
│              localhost:11434         │
└──────────────────────────────────────┘
```

The model never touches Neo4j. It emits OpenAI-style `tool_calls`; Omnigent executes them
against the local MCP server, which runs NextNode's fixed Cypher queries.

## 3. Decisions

| Decision | Choice | Why |
|---|---|---|
| Model | `Qwen/Qwen3-4B-Instruct-2507` | Apache-2.0 and ungated (no HF token), strong tool calling for its size, ~8 GB weights fit one 16–24 GB GPU. Non-thinking variant: no `<think>` blocks in agent JSON. |
| Tool calling | `--enable-auto-tool-choice --tool-call-parser=hermes` | Required for vLLM to turn Qwen3's tool-call tokens into OpenAI `tool_calls`; without it the Reasoner cannot reach MCP. |
| Context | `--max-model-len=16384` | Prompts + 7 MCP tool schemas + tool results stay well under it; smaller KV cache lets a T4 work. |
| Which agents | orchestrator, parser, reasoner → vLLM | No Gemini quota anywhere in the workflow. |
| Reviewer | `llama3.1:8b` on local Ollama | Phase 3 requires the reviewer to be a different model family from the reasoner. With the reasoner now on Qwen, the reviewer moves off Qwen. |
| Exposure | ClusterIP + `kubectl port-forward --address 127.0.0.1` | No public endpoint, no Ingress, no TLS certificates to manage; access is gated by the user's cluster RBAC. |
| Auth | `VLLM_API_KEY` from Secret `vllm-auth` | Defense in depth if someone else in the cluster can reach the Service. The scripts generate it randomly and pipe it to kubectl on stdin; it never appears in argv, on disk, or in this repo. |
| Request logs | off (vLLM default since v0.10.2) | Prompts contain resume text. |
| Rollout | `Recreate`, 1 replica | One GPU and a ReadWriteOnce cache: two pods would deadlock. |
| Cost control | `kubectl -n nextnode-inference scale deploy/vllm --replicas=0` when idle | GPU hours are the only cost; the PVC keeps weights so scale-up skips the download. |

## 4. Files

| File | Purpose |
|---|---|
| `deploy/k8s/vllm/{namespace,pvc,deployment,service,kustomization}.yaml` | `kubectl apply -k deploy/k8s/vllm` |
| `scripts/vllm_tunnel.ps1`, `scripts/vllm_tunnel.sh` | Deploy (optional), create the key Secret, wait for rollout, open and health-check the tunnel |
| `omnigent/nextnode_curriculum/**/config.yaml` | `model: qwen3-4b`, gateway `vllm-gpu`; reviewer `llama3.1:8b` on `ollama-local` |
| `scripts/omnigent_up.*` | Now also check the tunnel (`/health` on the vLLM port) before starting |
| `tests/test_vllm_deploy.py` | Static checks of the manifests and their agreement with the agent configs |

## 5. Runbook

```bash
# once: cluster credentials, then deploy + tunnel
kubectl config current-context
.\scripts\vllm_tunnel.ps1 -Deploy                  # macOS/Linux: scripts/vllm_tunnel.sh deploy

# once: add the gateway (key from the Secret; decode the base64 yourself)
kubectl -n nextnode-inference get secret vllm-auth -o jsonpath="{.data.api-key}"
omnigent setup        # gateway vllm-gpu, base URL http://localhost:8000/v1
ollama pull llama3.1:8b

# every session
.\scripts\vllm_tunnel.ps1                          # terminal 1, keep open
.\scripts\omnigent_up.ps1 -Run -UserId u1 -JobId j1 -ResumeFile cv.txt -JobFile jd.txt
```

**Port 8000 clash:** the NextNode API also defaults to 8000. If it is running, use
`-LocalPort 8001` (or `LOCAL_PORT=8001`), pass `-VllmPort 8001` to `omnigent_up`, and set the
`vllm-gpu` gateway's base URL to `http://localhost:8001/v1`.

## 6. Risks

| Risk | Mitigation |
|---|---|
| A 4B model is weaker at multi-step tool use than Gemini Flash | Reasoner prompt has explicit tool-use rules; the reviewer re-checks every number against the graph; one revision max. Upgrade path: Qwen3-8B on a 24 GB GPU (change `--model` and `--served-model-name` plus the agents' `model:`). |
| The MCP server's tool calls have only been exercised by a test client, not yet by a model | First Omnigent run on a local model (Ollama) before spending GPU credits. |
| Port-forward drops on idle or network change | The script exits with the tunnel; rerun it. `omnigent_up` checks `/health` first. |
| Cluster GPU node labels differ by cloud | The Deployment only tolerates `nvidia.com/gpu`; a commented GKE `nodeSelector` shows where to pin. |
| Image drift | Image is pinned to `v0.11.0`; bump deliberately and rerun the tests. |

## 7. Not in this phase
- Pointing the NextNode API's `llm.py` at vLLM (it uses the google-genai SDK today; an
  OpenAI-compatible provider behind the same limiter and cache would remove the API's
  rate limit too).
- Autoscaling to zero (KEDA / Knative); manual `scale` is enough for one developer.
