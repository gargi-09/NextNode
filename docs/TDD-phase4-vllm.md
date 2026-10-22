# TDD — Phase 4: Self-hosted inference with vLLM

Status: manifests, agent configs, tunnel scripts and the AWS EKS setup are written and statically
tested (the eksctl config passes `--dry-run`); GPU quota approved (4 vCPUs, us-east-1); not yet
deployed. A $0 local rehearsal (section 5) runs everything except the GPU before AWS. Builds on [TDD-phase3-omnigent.md](TDD-phase3-omnigent.md).

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
| Cost control | `scripts/aws_gpu.ps1 pause` (GPU node to 0) between sessions, `down` (delete the cluster) between days | Scaling only the Deployment to 0 does **not** stop billing: the GPU node keeps running. The EKS control plane bills ~$0.10/h even with 0 nodes, so delete the cluster when idle for more than a day. |

## 4. Files

| File | Purpose |
|---|---|
| `deploy/k8s/vllm/{namespace,pvc,deployment,service,kustomization}.yaml` | `kubectl apply -k deploy/k8s/vllm` |
| `scripts/vllm_tunnel.ps1`, `scripts/vllm_tunnel.sh` | Deploy (optional), create the key Secret, wait for rollout, open and health-check the tunnel |
| `omnigent/nextnode_curriculum/**/config.yaml` | `model: qwen3-4b`, gateway `vllm-gpu`; reviewer `llama3.1:8b` on `ollama-local` |
| `scripts/omnigent_up.*` | Now also check the tunnel (`/health` on the vLLM port) before starting |
| `deploy/aws/eksctl-cluster.yaml` | EKS cluster: one g6.xlarge managed node group (min 0), EBS CSI add-on with its IAM role, no NAT gateway |
| `deploy/aws/storageclass-gp3.yaml` | Default encrypted gp3 StorageClass (new EKS clusters have none, so the PVC would stay Pending) |
| `scripts/aws_gpu.ps1`, `scripts/aws_gpu.sh` | `status` / `up` / `pause` / `down`, with the current hourly cost |
| `tests/test_vllm_deploy.py` | Static checks of the manifests and their agreement with the agent configs |
| `deploy/k8s/vllm-local/` | Kustomize overlay: the same deployment on a laptop cluster, CPU image, Qwen3-0.6B, no GPU |
| `scripts/vllm_standin.ps1`, `scripts/vllm_standin.sh` | Qwen3-4B on Ollama at 127.0.0.1:8000 as `qwen3-4b`: the agents run unchanged without a GPU |
| `scripts/vllm_smoke.py` | Smoke test for any of the three: auth, chat, tool calls, and (`--mcp`) the model driving the real MCP tools |
| `omnigent/samples/` | A sample resume and job description for the first workflow run |
| `tests/test_vllm_local.py` | The overlay, the stand-in and the smoke test's checks (fake server, no model needed) |

## 5. Runbook

### Before AWS: local rehearsal ($0, no GPU)

Three stages, each catching a different class of bug before GPU hours are spent. All three
use the same port (`localhost:8000/v1`), model name (`qwen3-4b`) and smoke test as the cloud.

| Stage | What runs | What it proves | What it does not |
|---|---|---|---|
| 1. Static | `pytest`, `kubectl kustomize` | Manifests, scripts and agent configs agree | Anything at runtime |
| 2. vLLM on local Kubernetes | `deploy/k8s/vllm-local`: the same manifests on Docker Desktop with vLLM's CPU image and Qwen3-0.6B | Kustomize apply, Secret + API-key auth, probes, rollout, loopback tunnel, OpenAI API, hermes tool-call parsing | GPU scheduling, EBS, model quality |
| 3. Agents on the real model | `scripts/vllm_standin.*`: Qwen3-4B-Instruct-2507 (4-bit) on Ollama, served as `qwen3-4b` on 127.0.0.1:8000 | Qwen3-4B drives the real MCP tools against Neo4j; the full Omnigent workflow | vLLM itself (that was stage 2) |

`scripts/vllm_smoke.py` checks each stage: API reachable, 401 without the key (vLLM only),
model name, a chat reply, a structured `tool_calls` entry and, with `--mcp`, a full loop where
the model calls the MCP tools until it reports the graph's fit score for a fixed test user and
job (`smoke-user`, `smoke-job`, idempotent upserts). The same command then verifies the GPU
cluster after `aws_gpu up`.

Safety rails: `vllm_tunnel -Deploy -Local` refuses a non-laptop context, and plain `-Deploy`
refuses a laptop context (the GPU pod would stay Pending). The CPU image has no v0.11.0 build;
v0.11.1 is the nearest. Laptop memory: stop Docker Desktop before stage 3 (Qwen3-4B + Llama 3.1
8B on Ollama need about 9 GB).

### AWS (EKS)

| Item | Cost (us-east-1, on-demand, approx.) |
|---|---|
| g6.xlarge (NVIDIA L4 24 GB, 4 vCPU, 16 GiB) | ~$0.80/h, only while the node runs |
| EKS control plane | ~$0.10/h whenever the cluster exists, even with 0 nodes |
| Public IPv4 address per node | ~$0.005/h |
| EBS: 100 GiB node disk (gone with the node) + 30 GiB model cache | ~$0.08/GiB-month |
| NAT gateway | $0: disabled on purpose (nodes in public subnets, no inbound access) |

A 3-hour session costs about $3. A forgotten running node costs about $22/day; a paused
cluster about $2.40/day. A budget alert is set on the account.

One-time: an IAM user (not Identity Center: enabling AWS Organizations ends the free plan and
expires its credits), `aws configure --profile nextnode`, `setx AWS_PROFILE nextnode`, and a
quota of 4 vCPUs for "Running On-Demand G and VT instances" (new accounts start at 0; the
first request was denied and approved on appeal with a detailed use case).

```bash
# every session
.\scripts\aws_gpu.ps1 up                           # create (15-20 min) or resume (~5 min); prints cost
.\scripts\vllm_tunnel.ps1 -Deploy                  # first time on a new cluster; later just the tunnel
...
.\scripts\aws_gpu.ps1 pause                        # end of session: GPU node to 0
.\scripts\aws_gpu.ps1 down                         # done for days: delete everything, $0/h
```

A new cluster gets a new random vLLM API key. Nothing to update: `omnigent_up -KeyFromCluster`
reads the current key from the Secret on every run and stores it in Omnigent's secret store
(OS keychain) for the `vllm-gpu` provider. Omnigent filters its runners' environment, so an
environment variable would not reach them.

### Any cluster

```bash
# once: cluster credentials, then deploy + tunnel
kubectl config current-context
.\scripts\vllm_tunnel.ps1 -Deploy                  # macOS/Linux: scripts/vllm_tunnel.sh deploy

# once: the two providers from omnigent/providers.example.yaml in ~/.omnigent/config.yaml (no secrets)
ollama pull llama3.1:8b

# every session
.\scripts\vllm_tunnel.ps1                          # terminal 1, keep open
.\scripts\omnigent_up.ps1 -KeyFromCluster -Run -UserId u1 -JobId j1 -ResumeFile cv.txt -JobFile jd.txt
```

**Port 8000 clash:** the NextNode API also defaults to 8000. If it is running, use
`-LocalPort 8001` (or `LOCAL_PORT=8001`), pass `-VllmPort 8001` to `omnigent_up`, and set the
`vllm-gpu` provider's `base_url` in `~/.omnigent/config.yaml` to `http://localhost:8001/v1`.

## 6. Risks

| Risk | Mitigation |
|---|---|
| A 4B model is weaker at multi-step tool use than Gemini Flash | Reasoner prompt has explicit tool-use rules; the reviewer re-checks every number against the graph; one revision max. Upgrade path: Qwen3-8B on a 24 GB GPU (change `--model` and `--served-model-name` plus the agents' `model:`). |
| The MCP server's tool calls have only been exercised by a test client, not yet by a model | First Omnigent run on a local model (Ollama) before spending GPU credits. |
| Port-forward drops on idle or network change | The script exits with the tunnel; rerun it. `omnigent_up` checks `/health` first. |
| Cluster GPU node labels differ by cloud | The Deployment only tolerates `nvidia.com/gpu`; commented EKS and GKE `nodeSelector`s show where to pin. |
| The pod doesn't fit the node | Requests (2 CPU, 8 GiB, 1 GPU; limit 13 GiB) are sized under a g6.xlarge's allocatable resources next to the system pods; a test enforces it. |
| g6.xlarge capacity is short in a zone | The node group spans two zones that offer g6.xlarge; `--dry-run` validates the config without creating anything. |
| A deleted cluster leaves a billing EBS volume | `down` deletes the namespace (and PVC) before the cluster, then checks for leftover volumes by tag. |
| Kubernetes service links inject `VLLM_PORT=tcp://...` (the Service is named `vllm`), which crashes vLLM at startup | `enableServiceLinks: false` on the pod; found by the local rehearsal, enforced by a test |
| Image drift | Image is pinned to `v0.11.0`; bump deliberately and rerun the tests. |

## 7. Not in this phase
- Pointing the NextNode API's `llm.py` at vLLM (it uses the google-genai SDK today; an
  OpenAI-compatible provider behind the same limiter and cache would remove the API's
  rate limit too).
- Autoscaling to zero (KEDA / Knative); manual `scale` is enough for one developer.
