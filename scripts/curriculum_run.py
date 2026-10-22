"""Run the NextNode curriculum workflow with the orchestration written as code.

Same agents as the Omnigent bundle (omnigent/nextnode_curriculum): their prompts, models,
providers and MCP tool allow-lists are read from those YAML files, so both runners stay in
sync. The difference is who decides the next step. In Omnigent an orchestrator LLM does;
on a 4B model it stalls after dispatching ("I will now wait..."). Here a fixed sequence does:

    parse resume + parse job  ->  reasoner (MCP read+write tools)  ->  reviewer (read-only tools)
    ->  at most one revision (reasoner fixes the blocking issues, reviewer checks again)

Guardrails enforced in code: each agent is offered only its allow-listed tools and any other
call is refused; the reviewer never gets a write tool; tool calls are capped per agent.

    venv\\Scripts\\python scripts\\curriculum_run.py --user-id demo-user --job-id demo-job ^
        --resume omnigent\\samples\\resume.txt --job omnigent\\samples\\jd.txt
    ... --key-from-cluster      # against the real vLLM tunnel (AWS)

Needs the model server on localhost:8000 (scripts/vllm_standin.* or scripts/vllm_tunnel.*), the
normal Ollama with llama3.1:8b, and NextNode's Neo4j settings (the app loads them itself; this
script never reads .env). The result is printed and saved to .omnigent-logs/.
"""
import argparse
import asyncio
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vllm_smoke import (NO_THINKING, key_from_cluster, start_mcp_server,  # noqa: E402
                        strip_think, to_openai_tools)

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "omnigent" / "nextnode_curriculum"
PROVIDERS = ROOT / "omnigent" / "providers.example.yaml"
OUT_DIR = ROOT / ".omnigent-logs"
WRITE_TOOLS = {"upsert_user_skills", "upsert_job_posting"}
TOOL_RESULT_CHARS = 6000          # keep the reasoner inside its 16k context
MAX_TOOL_CALLS = 25               # per agent run (Omnigent's server cap is 40 per session)


# ─────────────────────────── helpers (unit-tested) ───────────────────────────

def load_agent(name: str) -> dict:
    """An agent's prompt, model, provider and tool allow-list from the Omnigent bundle."""
    spec = yaml.safe_load((BUNDLE / "agents" / name / "config.yaml").read_text(encoding="utf-8"))
    tools = ((spec.get("tools") or {}).get("nextnode") or {}).get("tools") or []
    return {"name": name, "prompt": spec["prompt"], "model": spec["executor"]["model"],
            "provider": spec["executor"]["auth"]["name"], "tools": list(tools)}


def provider_urls() -> dict[str, str]:
    providers = yaml.safe_load(PROVIDERS.read_text(encoding="utf-8"))["providers"]
    return {name: entry["openai"]["base_url"].removesuffix("/v1") for name, entry in providers.items()}


def extract_json(text: str) -> dict:
    """The first JSON object in a model answer (tolerates ``` fences and prose around it)."""
    text = strip_think(text)
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if fenced:
        return json.loads(fenced.group(1))
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the answer")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("the answer's JSON is not an object")
    return obj


def text_tool_call(content: str | None, allowed: set[str]) -> list[dict]:
    """A tool call the model wrote as text instead of using the tool interface (Llama 3.1 on
    Ollama does this): `{"name": "<allowed tool>", "parameters"|"arguments": {...}}`.
    Returned in OpenAI `tool_calls` shape so it runs like a normal call; [] otherwise."""
    try:
        obj = extract_json(content or "")
    except (ValueError, json.JSONDecodeError):
        return []
    args = obj.get("parameters", obj.get("arguments"))
    if obj.get("name") in allowed and isinstance(args, dict) and set(obj) <= {"name", "parameters", "arguments"}:
        return [{"id": f"text_{obj['name']}", "type": "function",
                 "function": {"name": obj["name"], "arguments": json.dumps(args)}}]
    return []


def require_verdict(review: dict):
    if str(review.get("verdict", "")).upper() not in {"PASS", "REVISE"}:
        raise ValueError('the review needs "verdict": "PASS" or "REVISE" and a "blocking" list')


def code_checks(curriculum: dict, gap: dict, prereqs: dict[str, list[str]],
                owned: set[str] | None = None) -> list[dict]:
    """The reviewer's seven checks, done in code against the graph (no model involved):
    fit score, roadmap coverage, prerequisite order, builds_on owned, priority/why copied,
    no URLs, hours. Numbered like the reviewer prompt's checks 1-7."""
    issues = []
    add = lambda check, module, issue: issues.append(   # noqa: E731
        {"check": check, "module": module, "issue": issue, "evidence": "code check against the graph"})
    if abs(float(curriculum.get("fit_score") or -1) - gap["fit_score"]) > 0.001 or \
            str(curriculum.get("scoring_version")) != str(gap["scoring_version"]):
        add(1, None, f"fit_score/scoring_version must be {gap['fit_score']}/{gap['scoring_version']}")
    steps = {s["skill"]: s for s in gap["roadmap"]}
    modules = curriculum.get("modules") or []
    names = [m.get("skill") for m in modules]
    for m in modules:
        step = steps.get(m.get("skill"))
        if step is None:
            add(2, m.get("skill"), "not a roadmap skill")
            continue
        if owned is not None:   # owned now, or taught by an earlier module (learned by then)
            known = owned | set(names[:names.index(m["skill"])])
            missing = [s for s in m.get("builds_on") or [] if s not in known]
            if missing:
                add(4, m["skill"], f"builds_on lists skills the user neither has nor learns in an "
                                   f"earlier module: {', '.join(missing)}")
        if m.get("priority") != step["priority"] or m.get("why") != step["reason"]:
            add(5, m["skill"], f"priority/why must be copied: {step['priority']!r} / {step['reason']!r}")
        hours = m.get("estimated_hours")
        if not isinstance(hours, int) or not 1 <= hours <= 200:
            add(7, m["skill"], "estimated_hours must be an integer from 1 to 200")
        if re.search(r"https?://|www\.", json.dumps(m.get("activities") or [])):
            add(6, m["skill"], "activities must not contain URLs")
    for skill, step in steps.items():
        if step["priority"] in ("high", "medium") and skill not in names:
            add(2, skill, f"roadmap step with priority {step['priority']} has no module")
    for i, skill in enumerate(names):
        later = [p for p in prereqs.get(skill, []) if p in names[i + 1:]]
        if later:
            add(3, skill, f"comes before its prerequisite(s) {', '.join(later)}: reorder the modules")
    return issues


def offered_tools(agent: dict, mcp_tools) -> list:
    """Only the agent's allow-listed tools; the reviewer never sees a write tool."""
    allowed = set(agent["tools"])
    if agent["name"] == "reviewer":
        allowed -= WRITE_TOOLS
    return [t for t in mcp_tools if t.name in allowed]


# ─────────────────────────── agent runs ───────────────────────────

class Agent:
    def __init__(self, spec: dict, client: httpx.Client, call=None, mcp_tools=()):
        self.spec, self.client, self.call = spec, client, call
        self.tools = offered_tools(spec, mcp_tools)
        self.allowed = {t.name for t in self.tools}
        self.messages = [{"role": "system", "content": spec["prompt"]}]
        self.tool_calls: list[tuple[str, dict]] = []

    def _chat(self, max_tokens: int) -> dict:
        body = {"model": self.spec["model"], "messages": self.messages, "temperature": 0,
                "max_tokens": max_tokens, **NO_THINKING}
        if self.tools:
            body |= {"tools": to_openai_tools(self.tools), "tool_choice": "auto"}
        r = self.client.post("/v1/chat/completions", json=body)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]

    async def ask(self, content: str, max_tokens: int = 2048, validate=None) -> dict:
        """Send a message, run its tool calls through MCP, return the final answer as JSON.
        `validate(obj)` may raise ValueError; invalid JSON or a failed validation is retried
        once with the error message."""
        self.messages.append({"role": "user", "content": content})
        for attempt in range(2):
            msg = await self._run_tools(max_tokens)
            answer = strip_think(msg.get("content"))
            self.messages.append({"role": "assistant", "content": answer})
            try:
                obj = extract_json(answer)
                if validate:
                    validate(obj)
                return obj
            except ValueError as e:
                if attempt == 1:
                    raise RuntimeError(f"{self.spec['name']} gave no usable answer ({e}): {answer[:300]!r}") from e
                self.messages.append({"role": "user", "content":
                                      f"That answer is not usable: {e}. Reply with ONE JSON object only, "
                                      "in the output format from your instructions."})

    async def _run_tools(self, max_tokens: int) -> dict:
        while True:
            msg = self._chat(max_tokens)
            calls = msg.get("tool_calls") or text_tool_call(msg.get("content"), self.allowed)
            if not calls:
                return msg
            self.messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
            for tc in calls:
                name, raw = tc["function"]["name"], tc["function"].get("arguments") or "{}"
                if len(self.tool_calls) >= MAX_TOOL_CALLS:
                    result = "Error: tool-call limit reached. Answer now with what you have."
                elif name not in self.allowed:
                    result = f"Error: tool '{name}' is not allowed for the {self.spec['name']}."
                else:
                    try:
                        arguments = json.loads(raw) if isinstance(raw, str) else raw
                        self.tool_calls.append((name, arguments))
                        log(f"    {self.spec['name']} -> {name}")
                        _, result = await self.call(name, arguments)
                    except json.JSONDecodeError:
                        result = "Error: arguments are not valid JSON."
                self.messages.append({"role": "tool", "tool_call_id": tc.get("id", name),
                                      "content": result[:TOOL_RESULT_CHARS]})


def log(text: str):
    print(text, flush=True)


async def graph_facts(call, user_id: str, job_id: str, curriculum: dict) -> tuple[dict, dict, set]:
    """The deterministic gap analysis, each module's prerequisites and the user's skills,
    straight from the graph."""
    ok, text = await call("calculate_skill_gap", {"user_id": user_id, "job_id": job_id})
    if not ok:
        raise RuntimeError(f"calculate_skill_gap failed: {text[:300]}")
    gap = json.loads(text)
    prereqs = {}
    for m in curriculum.get("modules") or []:
        ok, text = await call("get_skill_prerequisites", {"skill": m.get("skill") or ""})
        rows = json.loads(text).get("prerequisites", []) if ok else []
        prereqs[m.get("skill")] = [r.get("skill") or r.get("name") if isinstance(r, dict) else r for r in rows]
    ok, text = await call("get_user_profile", {"user_id": user_id})
    owned = {s["name"] for s in json.loads(text).get("skills", [])} if ok else None
    return gap, prereqs, owned


async def run_workflow(user_id, job_id, resume_text, job_text, clients, call, mcp_tools) -> dict:
    """The fixed sequence. `clients` maps provider name -> httpx.Client."""
    specs = {n: load_agent(n) for n in ("parser", "reasoner", "reviewer")}
    client = lambda n: clients[specs[n]["provider"]]   # noqa: E731

    parsed = {}
    for kind, text in (("resume", resume_text), ("job", job_text)):
        if not text:
            continue
        t = time.monotonic()
        log(f"[parse] {kind} ...")
        parsed[kind] = await Agent(specs["parser"], client("parser")).ask(f"MODE: {kind.upper()}\n{text}", 1536)
        log(f"[parse] {kind} done ({time.monotonic() - t:.0f}s): {len(parsed[kind].get('skills') or parsed[kind].get('core_skills') or [])} skills")

    reasoner = Agent(specs["reasoner"], client("reasoner"), call, mcp_tools)
    task = f"user_id: {user_id}\njob_id: {job_id}\n"
    for kind in ("resume", "job"):
        if kind in parsed:
            task += f"\n{kind.upper()} JSON:\n{json.dumps(parsed[kind])}\n"
    t = time.monotonic()
    log("[reason] drafting the curriculum ...")
    curriculum = await reasoner.ask(task)
    log(f"[reason] done ({time.monotonic() - t:.0f}s): {len(curriculum.get('modules') or [])} modules")

    review, revisions = None, 0
    for round_ in range(2):                       # first review + at most one revision
        reviewer = Agent(specs["reviewer"], client("reviewer"), call, mcp_tools)
        t = time.monotonic()
        log(f"[review] round {round_ + 1} ...")
        review = await reviewer.ask(f"user_id: {user_id}\njob_id: {job_id}\n\nCURRICULUM JSON:\n"
                                    f"{json.dumps(curriculum)}", validate=require_verdict)
        # The verdict comes from the same seven checks done in code against the graph. On the
        # first local run, Llama 3.1 8B reported a failure for every check on a curriculum the
        # graph confirmed correct, so its findings are kept as advisory notes, not votes.
        found = code_checks(curriculum, *await graph_facts(call, user_id, job_id, curriculum))
        review["reviewer_claims"] = review.get("blocking") or []
        review["blocking"] = found
        review["verdict"] = "REVISE" if found else "PASS"
        log(f"[review] {review['verdict']} ({time.monotonic() - t:.0f}s): {len(found)} issue(s) from the "
            f"graph checks; the reviewer model claimed {len(review['reviewer_claims'])} (advisory)")
        if review["verdict"] == "PASS" or round_ == 1:
            break
        revisions += 1
        log("[revise] sending the blocking issues back to the reasoner ...")
        curriculum = await reasoner.ask("Reviewer issues to fix:\n" + json.dumps(review["blocking"]))

    return {"user_id": user_id, "job_id": job_id, "curriculum": curriculum, "review": review,
            "revisions": revisions, "parsed": parsed,
            "tool_calls": {"reasoner": [n for n, _ in reasoner.tool_calls]},
            "unmapped_skills": curriculum.get("unmapped_skills", [])}


async def main_async(args) -> int:
    resume_text = Path(args.resume).read_text(encoding="utf-8") if args.resume else ""
    job_text = Path(args.job).read_text(encoding="utf-8") if args.job else ""
    key = key_from_cluster() if args.key_from_cluster else "local-standin"
    urls = provider_urls()
    clients = {name: httpx.Client(base_url=url, timeout=args.timeout,
                                  headers={"Authorization": f"Bearer {key if name == 'vllm-gpu' else 'ollama'}"})
               for name, url in urls.items()}

    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    token = secrets.token_urlsafe(24)
    server = start_mcp_server(args.mcp_port, token)
    try:
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}, timeout=httpx.Timeout(120))
        url = f"http://127.0.0.1:{args.mcp_port}/mcp"
        async with http, streamable_http_client(url, http_client=http) as (r, w, _), ClientSession(r, w) as session:
            await session.initialize()

            async def call(name: str, arguments: dict) -> tuple[bool, str]:
                res = await session.call_tool(name, arguments)
                return not res.isError, "".join(c.text for c in res.content if getattr(c, "text", None))

            t = time.monotonic()
            result = await run_workflow(args.user_id, args.job_id, resume_text, job_text,
                                        clients, call, (await session.list_tools()).tools)
    finally:
        server.terminate()

    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"curriculum-{args.user_id}-{args.job_id}.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    verdict = str((result["review"] or {}).get("verdict", "?")).upper()
    print("\n" + json.dumps(result["curriculum"], indent=2))
    print(f"\nVERDICT: {verdict} after {result['revisions']} revision(s), {time.monotonic() - t:.0f}s total")
    for issue in (result["review"] or {}).get("blocking") or []:
        print(f"  open issue (check {issue.get('check')}): {issue.get('issue')}")
    if result["unmapped_skills"]:
        print("Unmapped skills (extend the ontology?):", ", ".join(map(str, result["unmapped_skills"])))
    print(f"Saved: {out}")
    return 0 if verdict == "PASS" else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run the NextNode curriculum workflow (orchestration as code).")
    p.add_argument("--user-id", required=True)
    p.add_argument("--job-id", required=True)
    p.add_argument("--resume", help="resume text file")
    p.add_argument("--job", help="job description text file")
    p.add_argument("--key-from-cluster", action="store_true", help="real vLLM: API key from the vllm-auth Secret")
    p.add_argument("--mcp-port", type=int, default=8766)
    p.add_argument("--timeout", type=float, default=900, help="seconds per model call (CPU is slow)")
    args = p.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
