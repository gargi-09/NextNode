"""Phase 4 local rehearsal: the CPU overlay, the Ollama stand-in and the smoke test.

No cluster, model or database needed: manifests are checked statically, and the smoke test's
checks run against fake HTTP responses and fake MCP tool calls. See docs/TDD-phase4-vllm.md §5.
"""
import asyncio
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "deploy" / "k8s" / "vllm"
LOCAL = ROOT / "deploy" / "k8s" / "vllm-local"
SCRIPTS = ROOT / "scripts"


def _args(container: dict) -> dict:
    return dict(a.removeprefix("--").split("=", 1) if "=" in a else (a.removeprefix("--"), True)
                for a in container["args"])


BASE_ARGS = _args(yaml.safe_load((BASE / "deployment.yaml").read_text(encoding="utf-8"))
                  ["spec"]["template"]["spec"]["containers"][0])
KUST = yaml.safe_load((LOCAL / "kustomization.yaml").read_text(encoding="utf-8"))
DEPLOY_OPS = {op["path"]: op for patch in KUST["patches"] if patch["target"]["kind"] == "Deployment"
              for op in yaml.safe_load(patch["patch"])}

spec = importlib.util.spec_from_file_location("vllm_smoke", SCRIPTS / "vllm_smoke.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


# ─────────────────────────── CPU overlay ───────────────────────────

def test_local_overlay_reuses_the_gpu_manifests_and_only_patches_the_pod_and_cache():
    assert KUST["resources"] == ["../vllm"]
    assert {p["target"]["kind"] for p in KUST["patches"]} == {"Deployment", "PersistentVolumeClaim"}
    assert sorted(p.name for p in LOCAL.iterdir()) == ["kustomization.yaml"]   # no Secret, nothing else


def test_local_overlay_serves_the_same_api_as_the_gpu_deployment():
    args = _args({"args": DEPLOY_OPS["/spec/template/spec/containers/0/args"]["value"]})
    for key in ("served-model-name", "port", "enable-auto-tool-choice", "tool-call-parser"):
        assert args[key] == BASE_ARGS[key], key
    assert "enable-log-requests" not in args
    image = DEPLOY_OPS["/spec/template/spec/containers/0/image"]["value"]
    assert re.fullmatch(r"public\.ecr\.aws/q9t5s3a7/vllm-cpu-release-repo:v\d+\.\d+\.\d+", image)


def test_local_overlay_needs_no_gpu_and_fits_docker_desktop():
    resources = DEPLOY_OPS["/spec/template/spec/containers/0/resources"]["value"]
    assert "nvidia.com/gpu" not in json.dumps(resources)
    assert float(resources["limits"]["memory"].removesuffix("Gi")) <= 6


@pytest.mark.skipif(not shutil.which("kubectl"), reason="kubectl not installed")
def test_local_overlay_renders_with_kubectl():
    out = subprocess.run(["kubectl", "kustomize", str(LOCAL)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    docs = {d["kind"]: d for d in yaml.safe_load_all(out.stdout)}
    assert docs["Service"]["spec"]["type"] == "ClusterIP"
    pod = docs["Deployment"]["spec"]["template"]["spec"]
    assert "nvidia.com/gpu" not in json.dumps(pod["containers"][0]["resources"])
    assert {e["name"] for e in pod["containers"][0]["env"]} >= {"VLLM_API_KEY", "VLLM_CPU_KVCACHE_SPACE"}


def test_tunnel_scripts_keep_local_and_gpu_manifests_apart():
    for script, flag in (("vllm_tunnel.ps1", "-Local"), ("vllm_tunnel.sh", "deploy-local")):
        text = (SCRIPTS / script).read_text(encoding="utf-8")
        assert flag in text and "vllm-local" in text and "docker-desktop" in text, script


# ─────────────────────────── Ollama stand-in ───────────────────────────

def test_standin_serves_the_same_model_name_on_loopback():
    for script in ("vllm_standin.ps1", "vllm_standin.sh"):
        text = (SCRIPTS / script).read_text(encoding="utf-8")
        assert "127.0.0.1" in text and "0.0.0.0" not in text, script
        assert BASE_ARGS["served-model-name"] in text, script
        assert "4b-instruct-2507" in text and "2507" in BASE_ARGS["model"], script   # same model as vLLM
        assert BASE_ARGS["max-model-len"] in text, script                          # same context window
        assert "llama3.1:8b" in text, script                                        # the Reviewer's model
        assert ".env" not in text and "\x0b" not in text, script


def test_omnigent_up_accepts_the_tunnel_or_the_standin():
    for script in ("omnigent_up.ps1", "omnigent_up.sh"):
        text = (SCRIPTS / script).read_text(encoding="utf-8")
        assert "/v1/models" in text and "vllm_standin" in text, script


# ─────────────────────────── smoke test helpers ───────────────────────────

def test_helpers():
    assert smoke.strip_think("<think>hmm\n</think> OK") == "OK"
    assert smoke.mentions_score("The fit score is 0.551.", 0.551)
    assert smoke.mentions_score("about 55%", 0.551)
    assert not smoke.mentions_score("0.417", 0.551)
    assert smoke.first_tool_call({"tool_calls": [{"function": {"name": "f", "arguments": '{"a": 1}'}}]}) == ("f", {"a": 1})
    assert smoke.first_tool_call({"content": "text"}) is None
    tool = SimpleNamespace(name="t", description=None, inputSchema={"type": "object"})
    assert smoke.to_openai_tools([tool]) == [{"type": "function", "function": {
        "name": "t", "description": "", "parameters": {"type": "object"}}}]


def _fake_server(*, health=200, anon=401, tool_call=True, answers=()):
    """A fake OpenAI-compatible server. `answers` are the chat replies of the MCP loop, in order."""
    replies = iter(answers)

    def handler(request: httpx.Request) -> httpx.Response:
        authed = request.headers.get("authorization") == "Bearer k"
        if request.url.path == "/health":
            return httpx.Response(health)
        if request.url.path == "/v1/models":
            if health == 200 and not authed:
                return httpx.Response(anon, json={"data": [{"id": "qwen3-4b"}]})
            return httpx.Response(200, json={"data": [{"id": "qwen3-4b"}]})
        body = json.loads(request.content)
        if "tools" not in body:
            msg = {"role": "assistant", "content": "<think></think>OK"}
        elif body["tools"] == [smoke.PREREQ_TOOL]:
            msg = {"role": "assistant", "content": None if tool_call else "I would call the tool.",
                   "tool_calls": [{"id": "c1", "type": "function", "function": {
                       "name": "get_skill_prerequisites", "arguments": '{"skill": "PyTorch"}'}}] if tool_call else []}
        else:
            msg = next(replies)
        return httpx.Response(200, json={"choices": [{"message": msg}]})
    return httpx.MockTransport(handler)


ARGS = SimpleNamespace(host="127.0.0.1", port=8000, model="qwen3-4b", timeout=5, max_rounds=3)


def _run_basic(transport, key="k"):
    report = smoke.Report()
    client = smoke.check_server(ARGS, report, key, transport=transport)
    if client:
        smoke.check_chat(client, ARGS, report)
        smoke.check_tool_call(client, ARGS, report)
    return report, client


def test_smoke_passes_on_a_healthy_vllm():
    report, client = _run_basic(_fake_server())
    assert client and report.failed == 0


def test_smoke_flags_vllm_without_an_api_key():
    report, _ = _run_basic(_fake_server(anon=200))
    assert report.failed == 1


def test_smoke_needs_a_key_for_vllm_but_not_for_the_standin():
    assert _run_basic(_fake_server(), key="")[1] is None
    report, client = _run_basic(_fake_server(health=404), key="")
    assert client and report.failed == 0


def test_smoke_flags_a_model_that_answers_in_text_instead_of_a_tool_call():
    report, _ = _run_basic(_fake_server(tool_call=False))
    assert report.failed == 1


def _loop(answers):
    async def call(name, arguments):
        if name == "calculate_skill_gap":
            return True, json.dumps({"fit_score": 0.551})
        return True, json.dumps({"unmapped_skills": []})

    report = smoke.Report()
    client = httpx.Client(base_url="http://x", transport=_fake_server(answers=answers))
    tools = [SimpleNamespace(name="calculate_skill_gap", description="", inputSchema={"type": "object"})]
    asyncio.run(smoke.run_tool_loop(client, ARGS, report, call, tools))
    return report


GAP_CALL = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
    "name": "calculate_skill_gap", "arguments": '{"user_id": "smoke-user", "job_id": "smoke-job"}'}}]}


def test_mcp_loop_passes_when_the_model_uses_the_tool_and_copies_the_score():
    assert _loop([GAP_CALL, {"role": "assistant", "content": "The fit score is 0.551."}]).failed == 0


def test_mcp_loop_fails_when_the_model_guesses():
    assert _loop([{"role": "assistant", "content": "Probably 0.8."}]).failed == 2
