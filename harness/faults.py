"""Deterministic fault injection for the eval tasks (eval/tasks.json). Never active in a normal run.

A task's `faults` block, e.g.
    {"tool": {"calculate_skill_gap": {"code": "TIMEOUT", "times": 1}},
     "model": {"reasoner": {"kind": "malformed", "times": 1}},
     "draft": "reverse_modules", "revision": "reverse_modules_and_fit"}

  tool      the first `times` calls of that tool fail with `code` (TIMEOUT, GRAPH_UNAVAILABLE, ...)
            before reaching the MCP server; times = -1 means every call
  model     the first `times` answers of that agent are replaced by unparseable text
  draft     corrupts the reasoner's first draft before verification
  revision  corrupts the revised draft (used to exercise rollback)
"""
import copy

MALFORMED = "Sure! Here is the plan you asked for, let me know if you need anything else."


class Faults:
    def __init__(self, spec: dict | None = None):
        spec = spec or {}
        self._tool = {name: dict(f) for name, f in (spec.get("tool") or {}).items()}
        self._model = {name: dict(f) for name, f in (spec.get("model") or {}).items()}
        self.draft, self.revision = spec.get("draft"), spec.get("revision")
        self.injected: list[str] = []

    @staticmethod
    def _take(entry: dict) -> bool:
        times = entry.get("times", 1)
        if times == 0:
            return False
        if times > 0:
            entry["times"] = times - 1
        return True

    def tool_fault(self, name: str) -> str | None:
        entry = self._tool.get(name)
        if entry and self._take(entry):
            self.injected.append(f"tool:{name}:{entry['code']}")
            return entry["code"]
        return None

    def model_fault(self, agent: str) -> str | None:
        entry = self._model.get(agent)
        if entry and self._take(entry):
            self.injected.append(f"model:{agent}:{entry.get('kind', 'malformed')}")
            return entry.get("kind", "malformed")
        return None

    def corrupt(self, stage: str, curriculum: dict) -> dict:
        kind = self.draft if stage == "draft" else self.revision if stage == "revision" else None
        if not kind:
            return curriculum
        self.injected.append(f"{stage}:{kind}")
        out = copy.deepcopy(curriculum)
        modules = list(reversed(out.get("modules") or []))
        for i, m in enumerate(modules, 1):
            m["order"] = i
        out["modules"] = modules
        if kind == "reverse_modules_and_fit":
            out["fit_score"] = round(float(out.get("fit_score") or 0) + 0.25, 3)
        return out
