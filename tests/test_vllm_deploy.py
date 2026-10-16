"""Phase 4: the vLLM manifests are safe to apply and agree with the Omnigent agent configs.

Static checks only (no cluster needed). See docs/TDD-phase4-vllm.md.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
K8S = ROOT / "deploy" / "k8s" / "vllm"
BUNDLE = ROOT / "omnigent" / "nextnode_curriculum"
NS = "nextnode-inference"


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


DEPLOY = _load(K8S / "deployment.yaml")
SERVICE = _load(K8S / "service.yaml")
POD = DEPLOY["spec"]["template"]["spec"]
VLLM = POD["containers"][0]
ARGS = dict(a.removeprefix("--").split("=", 1) if "=" in a else (a.removeprefix("--"), True)
            for a in VLLM["args"])
ORCH = _load(BUNDLE / "config.yaml")
AGENTS = {"orchestrator": ORCH, **{n: _load(BUNDLE / "agents" / n / "config.yaml")
                                    for n in ORCH["tools"]["agents"]}}


def test_kustomization_lists_every_manifest_in_one_namespace():
    kust = _load(K8S / "kustomization.yaml")
    files = {p.name for p in K8S.glob("*.yaml")} - {"kustomization.yaml"}
    assert set(kust["resources"]) == files
    for name in files:
        doc = _load(K8S / name)
        assert (doc["metadata"].get("namespace") or doc["metadata"]["name"]) == NS, name
    assert not any(_load(K8S / n)["kind"] == "Secret" for n in files)   # key is created by the script


def test_service_is_private_and_routes_to_the_pod():
    assert SERVICE["spec"]["type"] == "ClusterIP"
    assert not list(K8S.glob("*ingress*"))
    assert SERVICE["spec"]["selector"].items() <= DEPLOY["spec"]["template"]["metadata"]["labels"].items()
    assert DEPLOY["spec"]["selector"]["matchLabels"] == SERVICE["spec"]["selector"]
    port = SERVICE["spec"]["ports"][0]
    assert port["port"] == 8000
    assert port["targetPort"] in {p["name"] for p in VLLM["ports"]}
    assert int(ARGS["port"]) == VLLM["ports"][0]["containerPort"] == 8000


def test_vllm_serves_openai_tool_calls_on_one_gpu():
    assert re.fullmatch(r"vllm/vllm-openai:v\d+\.\d+\.\d+", VLLM["image"])   # pinned, never :latest
    assert ARGS["enable-auto-tool-choice"] is True
    assert ARGS["tool-call-parser"] == "hermes"          # Qwen3 tool-call format
    assert "enable-log-requests" not in ARGS             # prompts carry resume text
    for section in ("requests", "limits"):
        assert VLLM["resources"][section]["nvidia.com/gpu"] == "1"
    assert DEPLOY["spec"]["replicas"] == 1 and DEPLOY["spec"]["strategy"]["type"] == "Recreate"
    assert VLLM["startupProbe"]["httpGet"]["path"] == "/health"


def test_api_key_comes_from_a_secret_never_a_literal():
    env = {e["name"]: e for e in VLLM["env"]}
    assert env["VLLM_API_KEY"]["valueFrom"]["secretKeyRef"] == {"name": "vllm-auth", "key": "api-key"}
    assert "value" not in env["VLLM_API_KEY"]
    assert not any("api-key" in str(a) for a in VLLM["args"])
    assert POD["automountServiceAccountToken"] is False
    assert VLLM["securityContext"]["allowPrivilegeEscalation"] is False


def test_agents_use_the_self_hosted_model_and_no_gemini():
    served = ARGS["served-model-name"]
    for name in ("orchestrator", "parser", "reasoner"):
        ex = AGENTS[name]["executor"]
        assert ex["model"] == served, name
        assert ex["auth"]["name"] == "vllm-gpu", name
    for name, spec in AGENTS.items():
        assert "gemini" not in spec["executor"]["model"], name


def test_reviewer_is_a_different_model_family_than_the_reasoner():
    family = lambda m: re.match(r"[a-z]+", m.lower()).group(0)   # qwen3-4b → qwen, llama3.1:8b → llama
    assert family(AGENTS["reviewer"]["executor"]["model"]) != family(AGENTS["reasoner"]["executor"]["model"])


def test_reasoner_keeps_its_mcp_tools():
    mcp = AGENTS["reasoner"]["tools"]["nextnode"]
    assert mcp["type"] == "mcp" and mcp["url"].startswith("http://127.0.0.1:")
    assert {"calculate_skill_gap", "upsert_user_skills", "get_skill_prerequisites"} <= set(mcp["tools"])


def test_tunnel_scripts_bind_loopback_and_match_the_service():
    for script in ("vllm_tunnel.ps1", "vllm_tunnel.sh"):
        text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert "port-forward" in text and "--address" in text and "127.0.0.1" in text, script
        assert "svc/vllm" in text and NS in text, script
        assert "0.0.0.0" not in text, script
        assert "\x0b" not in text, script
