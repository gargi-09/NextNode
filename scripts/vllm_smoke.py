"""Smoke test for the model server the agents use (docs/TDD-phase4-vllm.md section 5).

Works against all three ways of serving `qwen3-4b` on localhost, so the same checks run before
and after AWS:
  - the local CPU rehearsal on Docker Desktop   .\\scripts\\vllm_tunnel.ps1 -Deploy -Local
  - the Ollama stand-in (the real Qwen3-4B)      .\\scripts\\vllm_standin.ps1
  - the GPU cluster on AWS                       .\\scripts\\vllm_tunnel.ps1

    venv\\Scripts\\python scripts\\vllm_smoke.py --key-from-cluster       # vLLM: auth, chat, tool call
    venv\\Scripts\\python scripts\\vllm_smoke.py --mcp                    # stand-in: + model drives the MCP tools
    venv\\Scripts\\python scripts\\vllm_smoke.py --key-from-cluster --mcp # GPU cluster: everything

Checks: the OpenAI-compatible API answers; vLLM rejects requests without its key; the model
is served as `qwen3-4b`; a chat completion returns text; the model emits a structured
`tool_calls` entry (vLLM's hermes parser works). With --mcp: starts the NextNode MCP server on
loopback with a one-off token, writes a fixed test user and job (`smoke-user`, `smoke-job`,
idempotent upserts into your Neo4j graph), and lets the model call the real MCP tools until it
reports a fit score, which must equal the graph's. Needs NextNode's Neo4j settings, which the
app loads itself; this script never reads .env.

The API key comes from VLLM_API_KEY or, with --key-from-cluster, from the `vllm-auth` Secret
via kubectl; it stays in memory and is never printed.
"""
import argparse
import asyncio
import base64
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "nextnode-inference"
SMOKE_USER = {"user_id": "smoke-user", "name": "Smoke Test",
              "skills": [{"name": "Python", "proficiency": 0.8}, {"name": "NumPy", "proficiency": 0.6},
                         {"name": "SQL", "proficiency": 0.6}]}
SMOKE_JOB = {"job_id": "smoke-job", "title": "ML Engineer (smoke test)", "seniority": "mid",
             "core_skills": [{"name": "Python", "weight": 0.6}, {"name": "PyTorch", "weight": 0.6}],
             "preferred_skills": [{"name": "Docker", "weight": 0.6}],
             "domains": ["ml_infrastructure"]}
PREREQ_TOOL = {"type": "function", "function": {
    "name": "get_skill_prerequisites",
    "description": "Skills that lead to `skill` through prerequisite chains, nearest first.",
    "parameters": {"type": "object", "properties": {"skill": {"type": "string", "description": "Skill name"}},
                   "required": ["skill"]}}}
NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}   # Qwen3 base models think by default
TOOL_RESULT_CHARS = 8000


# ─────────────────────────── helpers (unit-tested) ───────────────────────────

def strip_think(text: str | None) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def to_openai_tools(mcp_tools) -> list[dict]:
    """MCP tool definitions → the `tools` list of an OpenAI chat completion request."""
    return [{"type": "function", "function": {"name": t.name, "description": t.description or "",
                                              "parameters": t.inputSchema}} for t in mcp_tools]


def first_tool_call(message: dict) -> tuple[str, dict] | None:
    """(name, arguments) of the first structured tool call, or None if the model answered in text."""
    calls = message.get("tool_calls") or []
    if not calls:
        return None
    fn = calls[0]["function"]
    args = fn.get("arguments") or "{}"
    return fn["name"], (json.loads(args) if isinstance(args, str) else args)


def mentions_score(text: str, score: float) -> bool:
    """True if the text states `score` as a 0-1 number (any rounding to 2+ places) or a percent."""
    for raw in re.findall(r"\d+(?:\.\d+)?", text):
        value = float(raw)
        if abs(value - score) < 0.006 or abs(value - score * 100) < 0.6:
            return True
    return False


def key_from_cluster() -> str:
    out = subprocess.run(["kubectl", "-n", NAMESPACE, "get", "secret", "vllm-auth",
                          "-o", "jsonpath={.data.api-key}"], capture_output=True, text=True, timeout=30)
    if out.returncode != 0 or not out.stdout:
        sys.exit("ERROR: could not read Secret vllm-auth (wrong kubectl context, or not deployed yet)")
    return base64.b64decode(out.stdout).decode()


# ─────────────────────────── checks ───────────────────────────

class Report:
    def __init__(self):
        self.failed = 0

    def ok(self, name: str, detail: str = ""):
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))

    def fail(self, name: str, detail: str):
        self.failed += 1
        print(f"  FAIL  {name}: {detail}")


def chat(client: httpx.Client, model: str, messages: list, **extra) -> dict:
    body = {"model": model, "messages": messages, "temperature": 0, "max_tokens": 512, **NO_THINKING, **extra}
    r = client.post("/v1/chat/completions", json=body)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


def check_server(args, report: Report, key: str, transport=None) -> httpx.Client | None:
    base = f"http://{args.host}:{args.port}"
    probe = httpx.Client(base_url=base, timeout=10, transport=transport)   # no key on purpose
    try:
        health = probe.get("/health").status_code
    except httpx.HTTPError as e:
        report.fail("reachable", f"{base}: {e.__class__.__name__}. Is the tunnel or the stand-in running?")
        return None
    is_vllm = health == 200
    report.ok("reachable", f"{base}, {'vLLM' if is_vllm else 'not vLLM (Ollama stand-in?)'}")

    if is_vllm:
        anon = probe.get("/v1/models").status_code
        if anon == 401:
            report.ok("rejects requests without the API key")
        else:
            report.fail("rejects requests without the API key", f"got HTTP {anon}; VLLM_API_KEY is not set in the pod")
        if not key:
            report.fail("API key", "vLLM needs one: pass --key-from-cluster or set VLLM_API_KEY")
            return None

    client = httpx.Client(base_url=base, timeout=args.timeout, transport=transport,
                          headers={"Authorization": f"Bearer {key or 'unused'}"})
    r = client.get("/v1/models")
    ids = [m["id"] for m in r.json().get("data", [])] if r.status_code == 200 else []
    if args.model in ids or f"{args.model}:latest" in ids:
        report.ok("serves the agents' model name", args.model)
    else:
        report.fail("serves the agents' model name", f"HTTP {r.status_code}, models {ids}; expected {args.model}")
        return None
    return client


def check_chat(client: httpx.Client, args, report: Report):
    t = time.monotonic()
    try:
        msg = chat(client, args.model, [{"role": "user", "content": "Reply with the single word OK."}], max_tokens=32)
    except httpx.HTTPError as e:
        return report.fail("chat completion", str(e))
    text = strip_think(msg.get("content"))
    if text:
        report.ok("chat completion", f"{text[:40]!r}, {time.monotonic() - t:.1f}s")
    else:
        report.fail("chat completion", "empty answer")


def check_tool_call(client: httpx.Client, args, report: Report):
    t = time.monotonic()
    messages = [{"role": "user", "content": "What should I learn before PyTorch? Use the tool."}]
    try:
        msg = chat(client, args.model, messages, tools=[PREREQ_TOOL], tool_choice="auto")
    except httpx.HTTPError as e:
        return report.fail("structured tool call", str(e))
    call = first_tool_call(msg)
    if call and call[0] == "get_skill_prerequisites" and "skill" in call[1]:
        report.ok("structured tool call", f"{call[0]}({call[1]}), {time.monotonic() - t:.1f}s")
    else:
        report.fail("structured tool call", f"no tool_calls; the model wrote {strip_think(msg.get('content'))[:120]!r}. "
                                            "On vLLM check --enable-auto-tool-choice --tool-call-parser=hermes")


# ─────────────────────────── MCP end to end ───────────────────────────

def start_mcp_server(port: int, token: str) -> subprocess.Popen:
    python = Path(sys.executable)
    proc = subprocess.Popen([str(python), "-m", "app.mcp_server", "--transport", "http",
                             "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env={**os.environ, "NEXTNODE_MCP_TOKEN": token},
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(40):
        time.sleep(0.5)
        if proc.poll() is not None:
            sys.exit("ERROR: MCP server exited: " + proc.stderr.read().decode(errors="replace")[-800:])
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
    proc.kill()
    sys.exit("ERROR: MCP server did not become healthy")


async def check_mcp_loop(client: httpx.Client, args, report: Report):
    """Start the real MCP server, connect over Streamable HTTP with a one-off token, run the loop."""
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    token = secrets.token_urlsafe(24)
    server = start_mcp_server(args.mcp_port, token)
    try:
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"},
                                      timeout=httpx.Timeout(60))
        url = f"http://127.0.0.1:{args.mcp_port}/mcp"
        async with http, streamable_http_client(url, http_client=http) as (r, w, _), ClientSession(r, w) as session:
            await session.initialize()

            async def call(name: str, arguments: dict) -> tuple[bool, str]:
                res = await session.call_tool(name, arguments)
                return not res.isError, "".join(c.text for c in res.content if getattr(c, "text", None))

            await run_tool_loop(client, args, report, call, (await session.list_tools()).tools)
    finally:
        server.terminate()


async def run_tool_loop(client: httpx.Client, args, report: Report, call, mcp_tools):
    """Write the test fixtures, get the true fit score, then let the model find it with the tools.
    `call(name, arguments)` returns (ok, text), as an MCP tool call does."""
    ok, text = await call("upsert_user_skills", SMOKE_USER)
    ok2, text2 = await call("upsert_job_posting", SMOKE_JOB)
    if not (ok and ok2):
        return report.fail("MCP: write test user and job", (text if not ok else text2)[:300])
    ok, text = await call("calculate_skill_gap", {"user_id": "smoke-user", "job_id": "smoke-job"})
    if not ok:
        return report.fail("MCP: calculate_skill_gap", text[:300])
    truth = json.loads(text)["fit_score"]
    report.ok("MCP: graph reachable", f"smoke-user vs smoke-job fit_score {truth}")

    tools = to_openai_tools(mcp_tools)
    messages = [
        {"role": "system", "content": "You answer questions about the NextNode career graph. "
         "Get every number from a tool call; never guess. Call tools with the tool-calling interface."},
        {"role": "user", "content": "What is the fit score of user smoke-user for job smoke-job? "
         "Answer with the number."}]
    called, msg = [], {}
    t = time.monotonic()
    for _ in range(args.max_rounds):
        msg = chat(client, args.model, messages, tools=tools, tool_choice="auto", max_tokens=1024)
        if not msg.get("tool_calls"):
            break
        messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        for tc in msg["tool_calls"]:
            name, raw = tc["function"]["name"], tc["function"].get("arguments") or "{}"
            try:
                arguments = json.loads(raw) if isinstance(raw, str) else raw
                _, result = await call(name, arguments)
            except json.JSONDecodeError:
                arguments, result = raw, "Error: arguments are not valid JSON"
            called.append((name, arguments))
            messages.append({"role": "tool", "tool_call_id": tc.get("id", name),
                             "content": result[:TOOL_RESULT_CHARS]})
    answer = strip_think(msg.get("content"))
    elapsed = f"{time.monotonic() - t:.1f}s"

    if ("calculate_skill_gap", {"user_id": "smoke-user", "job_id": "smoke-job"}) in called:
        report.ok("MCP: model called calculate_skill_gap with the right ids", f"calls {[n for n, _ in called]}")
    else:
        report.fail("MCP: model called calculate_skill_gap with the right ids", f"calls {called}")
    if mentions_score(answer, truth):
        report.ok("MCP: model reported the graph's fit score", f"{answer[:80]!r}, {elapsed}")
    else:
        report.fail("MCP: model reported the graph's fit score", f"expected {truth}, got {answer[:160]!r}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Smoke test the model server the NextNode agents use.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--model", default="qwen3-4b")
    p.add_argument("--key-from-cluster", action="store_true", help="read the API key from the vllm-auth Secret")
    p.add_argument("--mcp", action="store_true", help="also let the model drive the real NextNode MCP tools")
    p.add_argument("--mcp-port", type=int, default=8766)
    p.add_argument("--max-rounds", type=int, default=5)
    p.add_argument("--timeout", type=float, default=600, help="seconds per completion (CPU is slow)")
    args = p.parse_args(argv)

    key = key_from_cluster() if args.key_from_cluster else os.environ.get("VLLM_API_KEY", "")
    report = Report()
    print(f"Smoke test: {args.model} on {args.host}:{args.port}")
    client = check_server(args, report, key)
    if client:
        check_chat(client, args, report)
        check_tool_call(client, args, report)
        if args.mcp:
            asyncio.run(check_mcp_loop(client, args, report))
    print("RESULT:", "FAIL" if report.failed or not client else "PASS")
    return 1 if report.failed or not client else 0


if __name__ == "__main__":
    sys.exit(main())
