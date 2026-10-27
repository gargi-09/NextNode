"""The harness loop: parse -> draft -> verify -> (revise, maybe roll back) -> ship or queue.

Every stage ends in a checkpoint. A failure is classified by where it happened:
  retrieval  the graph could not answer (tool errors, circuit open, missing user or job)
  model      a model gave no usable answer after its retry
  execution  the harness itself or the model server failed (timeouts, replay miss)
A finished run is either `shipped` (verdict PASS, nothing suspicious) or `needs_approval`
(REVISE after the one revision, a rollback, or too many unmapped skills), and queued.
"""
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from curriculum_run import Agent, graph_facts, load_agent, require_verdict
from harness import RUNS_DIR
from harness.approvals import ApprovalQueue
from harness.checkpoints import Checkpoints
from harness.evaluators import deterministic_verdict, reviewer_annotation, run_evaluators
from harness.faults import Faults
from harness.replay import ModelClient, ReplayMiss, Usage
from harness.tools import ToolRunner
from harness.tracing import Tracer


class StageError(Exception):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


@dataclass
class Task:
    id: str
    user_id: str
    job_id: str
    resume_text: str = ""
    job_text: str = ""
    resume: dict | None = None        # already parsed (skips the parser)
    job: dict | None = None
    faults: dict = field(default_factory=dict)
    expect: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Config:
    mode: str = "live"                 # live | record | replay
    reviewer: bool = True              # run the advisory reviewer model
    max_revisions: int = 1
    approval_unmapped: int = 3         # this many unmapped skills or more -> human approval
    tool_timeout_s: float = 60.0
    tool_attempts: int = 3


class Harness:
    def __init__(self, clients: dict, call, mcp_tools, config: Config | None = None, *, fixture=None,
                 runs_dir: Path = RUNS_DIR, approvals: ApprovalQueue | None = None, sleep=None,
                 tracer_factory=None):
        self.clients, self.mcp_call, self.mcp_tools = clients, call, list(mcp_tools)
        self.config, self.fixture, self.runs_dir = config or Config(), fixture, Path(runs_dir)
        self.approvals = approvals or ApprovalQueue(self.runs_dir.parent / "approvals.jsonl")
        self.sleep = sleep
        self.tracer_factory = tracer_factory or (lambda run_dir: Tracer(run_dir))
        self.specs = {n: load_agent(n) for n in ("parser", "reasoner", "reviewer")}

    def _model(self, agent: str, task: Task, usage: Usage, faults: Faults, tracer) -> ModelClient:
        http = self.clients.get(self.specs[agent]["provider"])
        return ModelClient(agent, mode=self.config.mode, http=http, fixture=self.fixture, task=task.id,
                           usage=usage, faults=faults, tracer=tracer)

    async def run(self, task: Task, run_id: str | None = None) -> dict:
        run_id = run_id or f"{task.id}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        run_dir = self.runs_dir / run_id
        cp, faults, usage = Checkpoints(run_dir), Faults(task.faults), Usage()
        saved = run_dir / "result.json"
        if cp.done("final") and saved.exists():      # already finished: never decide or queue twice
            return json.loads(saved.read_text(encoding="utf-8"))
        tracer = self.tracer_factory(run_dir)
        kwargs = {"faults": faults, "tracer": tracer}
        if self.sleep:
            kwargs["sleep"] = self.sleep
        tools = ToolRunner(self.mcp_call, max_attempts=self.config.tool_attempts,
                           timeout_s=self.config.tool_timeout_s, **kwargs)
        if self.config.mode == "record" and self.fixture is not None and cp.latest() is None:
            self.fixture.reset(task.id)
        t0 = time.monotonic()
        result = {"run_id": run_id, "task_id": task.id, "status": "failed", "verdict": None,
                  "failure_category": None, "error": None, "rollback": False, "revisions": 0}
        state = cp.latest()[1] if cp.latest() else {}
        if state:
            print(f"[harness] resuming {run_id} after stage {cp.latest()[0]}")
        try:
            with tracer.span("run", {"run.id": run_id, "task.id": task.id, "mode": self.config.mode}) as span:
                state = await self._stages(task, state, cp, tools, usage, faults, tracer)
                result.update(self._decide(run_id, task, state))
                cp.save("final", state | {"status": result["status"]})
                span.set(status=result["status"], verdict=result["verdict"] or "", rollback=result["rollback"])
        except StageError as e:
            result.update(failure_category=e.category, error=str(e))
        except ReplayMiss as e:
            result.update(failure_category="execution", error=str(e))
        except httpx.HTTPError as e:
            result.update(failure_category="execution", error=f"model server: {e.__class__.__name__}: {e}")
        finally:
            tracer.shutdown()
        result.update(
            latency_s=round(time.monotonic() - t0, 3),
            tokens=usage.as_dict("recorded" if self.config.mode == "replay" else "live"),
            tools=vars(tools.stats) | {"breakers_open": sorted(n for n, b in tools.breakers.items()
                                                               if b.state != "closed")},
            faults_injected=faults.injected, run_dir=str(run_dir), trace=getattr(tracer, "target", None),
            drift=usage.drift,
        )
        (run_dir / "result.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
        return result

    async def _stages(self, task, state, cp, tools, usage, faults, tracer) -> dict:
        reasoner = None

        if "parsed" not in state:
            with tracer.span("stage", {"stage": "parse"}):
                parsed = {}
                for kind, given, text in (("resume", task.resume, task.resume_text), ("job", task.job, task.job_text)):
                    if given is not None:
                        parsed[kind] = given
                    elif text:
                        agent = Agent(self.specs["parser"], self._model("parser", task, usage, faults, tracer))
                        parsed[kind] = await self._ask(agent, f"MODE: {kind.upper()}\n{text}", 1536)
                state["parsed"] = parsed
                cp.save("parsed", state)

        if "draft" not in state:
            with tracer.span("stage", {"stage": "draft"}):
                reasoner = self._reasoner(task, usage, faults, tracer, tools)
                draft = await self._ask(reasoner, self._task_text(task, state["parsed"]))
                state["draft"] = faults.corrupt("draft", draft)
                state["curriculum"] = state["draft"]
                cp.save("drafted", state)

        if "verdict" not in state:
            state.update(await self._verify(task, state["curriculum"], tools, usage, faults, tracer, revision=0))
            cp.save("verified", state)

        if state["verdict"] == "REVISE" and state.get("revisions", 0) < self.config.max_revisions:
            with tracer.span("stage", {"stage": "revise"}) as span:
                if reasoner is None:      # resumed run: the reasoner's conversation is gone, restate it
                    reasoner = self._reasoner(task, usage, faults, tracer, tools)
                    reasoner.messages.append({"role": "user", "content": self._task_text(task, state["parsed"])})
                    reasoner.messages.append({"role": "assistant", "content": json.dumps(state["curriculum"])})
                revised = await self._ask(reasoner, "Issues to fix:\n" + json.dumps(state["issues"]))
                revised = faults.corrupt("revision", revised)
                before = {k: state[k] for k in ("curriculum", "issues", "evals", "verdict")}
                state.update(await self._verify(task, revised, tools, usage, faults, tracer, revision=1),
                             curriculum=revised, revisions=state.get("revisions", 0) + 1)
                if len(state["issues"]) > len(before["issues"]):
                    print(f"[harness] revision made it worse ({len(before['issues'])} -> "
                          f"{len(state['issues'])} issues): rolling back to the previous draft")
                    state.update(before, rollback=True)
                span.set(rollback=state.get("rollback", False), issues=len(state["issues"]))
                cp.save("revised", state)
        return state

    def _reasoner(self, task, usage, faults, tracer, tools) -> Agent:
        return Agent(self.specs["reasoner"], self._model("reasoner", task, usage, faults, tracer),
                     tools.call, self.mcp_tools)

    @staticmethod
    def _task_text(task: Task, parsed: dict) -> str:
        text = f"user_id: {task.user_id}\njob_id: {task.job_id}\n"
        for kind in ("resume", "job"):
            if kind in parsed:
                text += f"\n{kind.upper()} JSON:\n{json.dumps(parsed[kind])}\n"
        return text

    @staticmethod
    async def _ask(agent: Agent, content: str, max_tokens: int = 2048, validate=None) -> dict:
        try:
            return await agent.ask(content, max_tokens, validate=validate)
        except RuntimeError as e:          # Agent.ask: no usable answer after its retry
            raise StageError("model", str(e)) from e

    async def _verify(self, task, curriculum, tools, usage, faults, tracer, revision: int) -> dict:
        with tracer.span("stage", {"stage": "verify", "revision": revision}) as span:
            try:
                gap, prereqs, owned = await graph_facts(tools.call, task.user_id, task.job_id, curriculum)
            except (RuntimeError, json.JSONDecodeError, KeyError) as e:
                raise StageError("retrieval", f"graph facts unavailable: {e}") from e
            payload = {"curriculum": curriculum, "gap": gap, "prereqs": prereqs,
                       "owned": sorted(owned) if owned is not None else None}
            evals = run_evaluators(payload)
            out = {"evals": [e.as_dict() for e in evals], "issues": payload["_issues"],
                   "verdict": deterministic_verdict(evals), "fit_score": gap["fit_score"],
                   "unmapped": await self._unmapped(tools, task)}
            if self.config.reviewer:
                reviewer = Agent(self.specs["reviewer"], self._model("reviewer", task, usage, faults, tracer),
                                 tools.call, self.mcp_tools)
                try:
                    review = await reviewer.ask(f"user_id: {task.user_id}\njob_id: {task.job_id}\n\n"
                                                f"CURRICULUM JSON:\n{json.dumps(curriculum)}", validate=require_verdict)
                    claims = review.get("blocking") or []
                except RuntimeError:
                    claims = [{"issue": "reviewer gave no usable answer"}]
                out["reviewer"] = reviewer_annotation(claims).as_dict() | {"claims": claims}
            span.set(verdict=out["verdict"], issues=len(out["issues"]))
            return out

    @staticmethod
    async def _unmapped(tools, task: Task) -> int:
        """Unmapped skill names stored on the user and the job: read from the graph, not from the
        model's curriculum, so the approval rule cannot be skipped by a model that drops them."""
        count = 0
        for tool, args in (("get_user_profile", {"user_id": task.user_id}),
                           ("get_job_posting", {"job_id": task.job_id})):
            ok, text = await tools.call(tool, args)
            if ok:
                count += len(json.loads(text).get("unmapped_skills") or [])
        return count

    def _decide(self, run_id: str, task: Task, state: dict) -> dict:
        reasons = []
        if state["verdict"] != "PASS":
            reasons.append(f"verdict REVISE with {len(state['issues'])} open issue(s)")
        if state.get("rollback"):
            reasons.append("revision made the draft worse and was rolled back")
        if state.get("unmapped", 0) >= self.config.approval_unmapped:
            reasons.append(f"{state['unmapped']} unmapped skills (ontology may be missing terms)")
        status = "needs_approval" if reasons else "shipped"
        if reasons:
            self.approvals.submit(run_id, reasons, {"task": task.id, "user_id": task.user_id,
                                                    "job_id": task.job_id, "verdict": state["verdict"],
                                                    "fit_score": state.get("fit_score")})
        return {"status": status, "verdict": state["verdict"], "approval_reasons": reasons,
                "rollback": state.get("rollback", False), "revisions": state.get("revisions", 0),
                "issues": state["issues"], "evals": state["evals"], "curriculum": state["curriculum"],
                "reviewer": state.get("reviewer")}
