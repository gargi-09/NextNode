"""Command line for the harness.

    python -m harness run --user-id demo-user --job-id demo-job --resume cv.txt --job jd.txt
    python -m harness run --resume-run <run_id> ...        # continue after the last checkpoint
    python -m harness approvals list
    python -m harness approvals approve <run_id> [--note TEXT]
    python -m harness approvals reject <run_id> [--note TEXT]
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from harness import RUNS_DIR
from harness.approvals import ApprovalQueue
from harness.loop import Config, Harness, Task
from harness.session import mcp_session, model_clients

QUEUE = ApprovalQueue(RUNS_DIR.parent / "approvals.jsonl")


async def _run(args) -> int:
    task = Task(id="cli", user_id=args.user_id, job_id=args.job_id,
                resume_text=Path(args.resume).read_text(encoding="utf-8") if args.resume else "",
                job_text=Path(args.job).read_text(encoding="utf-8") if args.job else "")
    config = Config(reviewer=not args.no_reviewer)
    async with mcp_session(args.mcp_port) as (call, tools):
        harness = Harness(model_clients(args.key_from_cluster), call, tools, config, approvals=QUEUE)
        result = await harness.run(task, run_id=args.resume_run)
    print("\n" + json.dumps(result.get("curriculum"), indent=2))
    print(f"\nSTATUS: {result['status']}   verdict: {result['verdict']}   "
          f"revisions: {result['revisions']}   rollback: {result['rollback']}   {result['latency_s']:.0f}s")
    for reason in result.get("approval_reasons") or []:
        print(f"  queued for approval: {reason}")
    if result["error"]:
        print(f"  failed ({result['failure_category']}): {result['error']}")
    t = result["tools"]
    print(f"  tools: {t['calls']} calls, {t['retries']} retries, {t['recovered']} recovered, "
          f"{t['breaker_opens']} breaker opens   tokens: {result['tokens']['total']}")
    print(f"  run dir: {result['run_dir']}\n  traces:  {result['trace']}")
    return 0 if result["status"] == "shipped" else 1


def _approvals(args) -> int:
    if args.action == "list":
        items = QUEUE.pending() if not args.all else list(QUEUE.items().values())
        if not items:
            print("No runs waiting for approval.")
        for i in items:
            s = i["summary"]
            print(f"{i['run_id']}  [{i['status']}]  {s.get('user_id')} -> {s.get('job_id')}  "
                  f"verdict {s.get('verdict')}  fit {s.get('fit_score')}")
            for r in i["reasons"]:
                print(f"    - {r}")
        return 0
    try:
        item = QUEUE.decide(args.run_id, approve=args.action == "approve", note=args.note or "")
    except (KeyError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"{item['run_id']}: {item['status']}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m harness", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the curriculum workflow once")
    r.add_argument("--user-id", required=True)
    r.add_argument("--job-id", required=True)
    r.add_argument("--resume", help="resume text file")
    r.add_argument("--job", help="job description text file")
    r.add_argument("--resume-run", help="continue this run id from its last checkpoint")
    r.add_argument("--no-reviewer", action="store_true", help="skip the advisory reviewer model")
    r.add_argument("--key-from-cluster", action="store_true", help="real vLLM: key from the vllm-auth Secret")
    r.add_argument("--mcp-port", type=int, default=8767)
    a = sub.add_parser("approvals", help="list or decide queued runs")
    a.add_argument("action", choices=["list", "approve", "reject"])
    a.add_argument("run_id", nargs="?")
    a.add_argument("--note")
    a.add_argument("--all", action="store_true", help="list decided runs too")
    args = p.parse_args(argv)
    if args.cmd == "approvals" and args.action != "list" and not args.run_id:
        p.error("approve/reject need a run_id")
    return asyncio.run(_run(args)) if args.cmd == "run" else _approvals(args)


if __name__ == "__main__":
    sys.exit(main())
