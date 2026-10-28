"""Run the 20 harness eval tasks and write measured results.

    venv\\Scripts\\python -m eval.run_eval --mode record     # once: live models, saves eval/fixtures/recorded.json
    venv\\Scripts\\python -m eval.run_eval --mode replay     # any time after: recorded model answers, real graph
    venv\\Scripts\\python -m eval.run_eval --only eval-13,eval-15
    venv\\Scripts\\python -m eval.run_eval --cleanup         # delete the eval-* users and jobs from Neo4j

Model answers are live (record) or replayed (replay); every tool call always goes to the real
MCP server and Neo4j. Results: eval/results/latest.md (table) and a JSON file per run, with
task success rate, p50/p95 latency, tokens per task (labelled live or recorded) and a failure
breakdown by cause. Only measured values are written; nothing is estimated.

Record mode saves each finished task (its recorded answers and its result row) as it goes, so
rerunning after a crash or Ctrl-C skips the tasks already recorded and keeps their live
measurements; --fresh records everything again. A task that raises is reported as a failed
row and the run continues.
"""
import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import RUNS_DIR  # noqa: E402
from harness.approvals import ApprovalQueue  # noqa: E402
from harness.loop import Config, Harness, Task  # noqa: E402
from harness.replay import Fixture  # noqa: E402

TASKS = ROOT / "eval" / "tasks.json"
FIXTURE = ROOT / "eval" / "fixtures" / "recorded.json"
RESULTS = ROOT / "eval" / "results"
EVAL_RUNS = RUNS_DIR.parent / "eval-runs"
PROGRESS = RESULTS / "record-progress.json"


def load_tasks(only: set[str] | None = None) -> list[dict]:
    tasks = json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]
    return [t for t in tasks if not only or t["id"] in only]


def check_expectation(result: dict, expect: dict) -> list[str]:
    """Why the run did not meet its expectation ([] means it passed)."""
    problems = []
    if result["status"] not in expect.get("status", ["shipped"]):
        problems.append(f"status {result['status']}, expected {' or '.join(expect['status'])}")
    if "failure_category" in expect and result.get("failure_category") != expect["failure_category"]:
        problems.append(f"failure category {result.get('failure_category')}, expected {expect['failure_category']}")
    if result["tools"]["recovered"] < expect.get("min_recovered", 0):
        problems.append(f"{result['tools']['recovered']} recovered tool call(s), expected >= {expect['min_recovered']}")
    if result["tools"]["breaker_opens"] < expect.get("min_breaker_opens", 0):
        problems.append(f"{result['tools']['breaker_opens']} breaker open(s), expected >= {expect['min_breaker_opens']}")
    if "rollback" in expect and result.get("rollback") != expect["rollback"]:
        problems.append(f"rollback {result.get('rollback')}, expected {expect['rollback']}")
    return problems


def cause(result: dict, passed: bool) -> str | None:
    """Failure cause for the breakdown: retrieval | model | execution (None when the task passed)."""
    if passed:
        return None
    if result["status"] == "failed":
        return result.get("failure_category") or "execution"
    return "model"          # finished but not as expected: the drafts did not reach the expected verdict


def crashed_result(error: str, latency_s: float) -> dict:
    """The result of a task whose run raised instead of returning (counted as execution)."""
    from harness.tools import ToolStats
    return {"status": "failed", "failure_category": "execution", "error": error, "verdict": None,
            "rollback": False, "latency_s": round(latency_s, 3), "faults_injected": [], "drift": 0,
            "tokens": {"prompt": 0, "completion": 0, "total": 0, "model_calls": 0, "source": "live"},
            "tools": vars(ToolStats())}


def load_progress(fixture, fresh: bool, path: Path = PROGRESS) -> dict:
    """Rows of tasks recorded by an earlier record run, keyed by task id; only those whose
    recorded answers are still in the fixture."""
    if fresh or fixture is None or not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in rows.items() if fixture.data.get(k)}


def resumable(row: dict) -> bool:
    """A recorded row is kept for reruns unless it failed for an execution reason (model server
    down, a crash): those tasks are recorded again."""
    return row["cause"] != "execution"


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def summarize(rows: list[dict], mode: str) -> dict:
    latencies = [r["latency_s"] for r in rows]
    tokens = [r["tokens"]["total"] for r in rows]
    passed = [r for r in rows if r["passed"]]
    causes: dict[str, int] = {}
    for r in rows:
        if r["cause"]:
            causes[r["cause"]] = causes.get(r["cause"], 0) + 1
    statuses: dict[str, int] = {}
    for r in rows:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    return {
        "mode": mode, "tasks": len(rows), "passed": len(passed),
        "success_rate": round(len(passed) / len(rows), 3) if rows else None,
        "statuses": statuses,
        "latency_s": {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95),
                      "what": "model + harness + graph" if mode != "replay"
                              else "harness + real MCP/Neo4j calls; model answers replayed"},
        "tokens_per_task": {"mean": round(sum(tokens) / len(tokens), 1) if tokens else None,
                            "source": "recorded" if mode == "replay" else "live"},
        "failure_causes": causes,
        "tool_totals": {k: sum(r["tools"][k] for r in rows) for k in ("calls", "retries", "recovered", "breaker_opens")},
        "replay_drift": sum(r.get("drift", 0) for r in rows),
    }


def markdown(summary: dict, rows: list[dict]) -> str:
    s = summary
    lines = [
        f"# Harness eval: {s['mode']} mode",
        "",
        f"Measured on {time.strftime('%Y-%m-%d %H:%M')}. Latency covers {s['latency_s']['what']}; "
        f"tokens are {s['tokens_per_task']['source']}.",
        "",
        "| Metric | Value |", "|---|---|",
        f"| Task success (outcome matched expectation) | {s['passed']}/{s['tasks']} ({s['success_rate']:.0%}) |",
        f"| Outcomes | " + ", ".join(f"{k} {v}" for k, v in sorted(s['statuses'].items())) + " |",
        f"| Latency p50 / p95 | {s['latency_s']['p50']:.1f} s / {s['latency_s']['p95']:.1f} s |",
        f"| Tokens per task (mean, {s['tokens_per_task']['source']}) | {s['tokens_per_task']['mean']} |",
        f"| Failure causes | " + (", ".join(f"{k} {v}" for k, v in sorted(s['failure_causes'].items())) or "none") + " |",
        f"| Tool calls / retries / recovered / breaker opens | " + " / ".join(str(v) for v in s['tool_totals'].values()) + " |",
        "",
        "| Task | Category | Outcome | Expected | Passed | Latency | Tokens | Notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        note = "; ".join(r["problems"]) or (r.get("error") or "")
        lines.append(f"| {r['task_id']} | {r['category']} | {r['status']} | {'/'.join(r['expected'])} | "
                     f"{'yes' if r['passed'] else 'no'} | {r['latency_s']:.1f} s | {r['tokens']['total']} | {note[:120]} |")
    return "\n".join(lines) + "\n"


async def run_all(args) -> int:
    from harness.session import mcp_session, model_clients

    mode = args.mode
    fixture = Fixture(FIXTURE) if mode in ("record", "replay") else None
    if mode == "replay" and not FIXTURE.exists():
        sys.exit(f"ERROR: no recording at {FIXTURE}; run --mode record first")
    config = Config(mode=mode, reviewer=args.reviewer)
    queue = ApprovalQueue(EVAL_RUNS / "approvals.jsonl")
    tasks = load_tasks(set(args.only.split(",")) if args.only else None)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    rows = []
    clients = model_clients(args.key_from_cluster) if mode != "replay" else {}
    progress = load_progress(fixture, args.fresh) if mode == "record" else {}
    async with mcp_session(args.mcp_port) as (call, tools):
        harness = Harness(clients, call, tools, config, fixture=fixture, runs_dir=EVAL_RUNS, approvals=queue)
        for i, spec in enumerate(tasks, 1):
            task = Task.from_dict(spec)
            print(f"\n=== [{i}/{len(tasks)}] {task.id}: {spec['description']}", flush=True)
            if task.id in progress:
                rows.append(progress[task.id])
                print(f"    -> already recorded ({progress[task.id]['status']}); --fresh records it again",
                      flush=True)
                continue
            t0 = time.monotonic()
            try:
                result = await harness.run(task, run_id=f"{task.id}-{mode}-{stamp}")
            except Exception as e:                           # one broken task must not end the eval
                result = crashed_result(f"crashed: {e.__class__.__name__}: {e}"[:300], time.monotonic() - t0)
            problems = check_expectation(result, spec.get("expect", {}))
            row = {"task_id": task.id, "category": spec["category"], "status": result["status"],
                   "expected": spec.get("expect", {}).get("status", ["shipped"]), "passed": not problems,
                   "problems": problems, "cause": cause(result, not problems), "latency_s": result["latency_s"],
                   "tokens": result["tokens"], "tools": result["tools"], "rollback": result["rollback"],
                   "verdict": result["verdict"], "error": result["error"], "faults": result["faults_injected"],
                   "drift": result.get("drift", 0)}
            rows.append(row)
            if fixture is not None and mode == "record":
                fixture.save()                               # keep what is recorded so far
                if resumable(row):
                    progress[task.id] = row
                    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
                    PROGRESS.write_text(json.dumps(progress, indent=1), encoding="utf-8")
            print(f"    -> {result['status']} ({'PASS' if row['passed'] else 'FAIL: ' + '; '.join(problems)}) "
                  f"in {result['latency_s']:.1f}s", flush=True)
    summary = summarize(rows, mode)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{mode}-{stamp}.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1),
                                                  encoding="utf-8")
    report = markdown(summary, rows)
    (RESULTS / "latest.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"Saved: {RESULTS / 'latest.md'} and {RESULTS / f'{mode}-{stamp}.json'}")
    return 0


async def cleanup() -> int:
    from app.core.errors import NotFoundError
    from app.services import jobs, users

    removed = 0
    for spec in load_tasks():
        for kind, ident, delete in (("user", spec["user_id"], users.delete_user), ("job", spec["job_id"], jobs.delete_job)):
            if not ident.startswith("eval-"):
                continue                                     # never touch anything outside the eval namespace
            try:
                await delete(ident)
                removed += 1
                print(f"deleted {kind} {ident}")
            except NotFoundError:
                pass
    print(f"Removed {removed} eval node(s).")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run the 20-task harness eval.")
    p.add_argument("--mode", choices=["record", "replay", "live"], default="replay")
    p.add_argument("--only", help="comma-separated task ids, e.g. eval-13,eval-15")
    p.add_argument("--reviewer", action="store_true", help="also run the advisory reviewer model (slower)")
    p.add_argument("--key-from-cluster", action="store_true")
    p.add_argument("--mcp-port", type=int, default=8768)
    p.add_argument("--fresh", action="store_true", help="record mode: record every task again")
    p.add_argument("--cleanup",action="store_true", help="delete the eval-* users and jobs, then exit")
    args = p.parse_args(argv)
    return asyncio.run(cleanup() if args.cleanup else run_all(args))


if __name__ == "__main__":
    sys.exit(main())
