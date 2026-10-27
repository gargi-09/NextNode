"""Deterministic evaluators, in the shape of the Phoenix evals project
(../phoenix-museum-qa-evals/src/phoenix_evals.py):

    evaluator(payload) -> EvalResult(label, score, explanation)

`payload` holds everything needed to recompute a verdict from the record of a run, not from
the agents' own claims: the curriculum and the graph facts fetched for it (gap analysis,
prerequisites, the user's skills). The seven evaluators are the reviewer prompt's checks 1-7,
computed by scripts/curriculum_run.code_checks. `deterministic_verdict` combines them; the
reviewer model's findings are kept separately as an advisory LLM annotation.
"""
from dataclasses import asdict, dataclass

from curriculum_run import code_checks

CHECKS = {
    1: "fit_score_copied",
    2: "roadmap_coverage",
    3: "prerequisite_order",
    4: "builds_on_owned",
    5: "priority_and_why_copied",
    6: "no_urls",
    7: "hours_in_range",
}


@dataclass
class EvalResult:
    name: str
    label: str               # "pass" | "fail"
    score: float             # 1.0 pass, 0.0 fail
    explanation: str
    annotator_kind: str = "CODE"

    def as_dict(self) -> dict:
        return asdict(self)


def _compute(payload: dict) -> list[dict]:
    owned = payload.get("owned")
    return code_checks(payload["curriculum"], payload["gap"], payload["prereqs"],
                       set(owned) if owned is not None else None)


def _issues(payload: dict) -> list[dict]:
    if "_issues" not in payload:            # a single evaluator called on its own
        payload["_issues"] = _compute(payload)
    return payload["_issues"]


def make_evaluator(check: int):
    name = CHECKS[check]

    def evaluator(payload: dict) -> EvalResult:
        found = [i for i in _issues(payload) if i["check"] == check]
        if not found:
            return EvalResult(name, "pass", 1.0, "no issues")
        return EvalResult(name, "fail", 0.0, "; ".join(
            f"{i['module']}: {i['issue']}" if i.get("module") else i["issue"] for i in found))

    evaluator.__name__ = name
    return evaluator


EVALUATORS = [make_evaluator(n) for n in CHECKS]


def run_evaluators(payload: dict) -> list[EvalResult]:
    payload["_issues"] = _compute(payload)  # always fresh: a copied payload may carry stale issues
    return [e(payload) for e in EVALUATORS]


def deterministic_verdict(results: list[EvalResult]) -> str:
    return "PASS" if all(r.label == "pass" for r in results) else "REVISE"


def reviewer_annotation(claims: list[dict]) -> EvalResult:
    """The reviewer model's findings, recorded but not voting (annotator_kind LLM)."""
    if not claims:
        return EvalResult("llm_reviewer", "pass", 1.0, "no claims", "LLM")
    return EvalResult("llm_reviewer", "fail", 0.0, f"{len(claims)} claim(s), advisory", "LLM")
