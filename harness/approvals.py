"""Approval queue: low-confidence results wait for a human instead of being shipped.

A file of JSON lines (.omnigent-logs/approvals.jsonl): every submit and decision is appended,
and the current state is derived by replaying the file, so nothing is ever overwritten.

    python -m harness approvals list
    python -m harness approvals approve <run_id> --note "looks right"
    python -m harness approvals reject <run_id> --note "wrong ordering"
"""
import json
import time
from pathlib import Path


class ApprovalQueue:
    def __init__(self, path: Path, clock=time.time):
        self.path, self.clock = Path(path), clock

    def _append(self, event: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({**event, "at": round(self.clock(), 3)}) + "\n")

    def _events(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def submit(self, run_id: str, reasons: list[str], summary: dict):
        self._append({"event": "submitted", "run_id": run_id, "reasons": reasons, "summary": summary})

    def items(self) -> dict[str, dict]:
        state: dict[str, dict] = {}
        for e in self._events():
            if e["event"] == "submitted":
                state[e["run_id"]] = {"run_id": e["run_id"], "status": "pending", "reasons": e["reasons"],
                                      "summary": e["summary"], "submitted_at": e["at"]}
            elif e["run_id"] in state:
                state[e["run_id"]].update(status=e["event"], note=e.get("note", ""), decided_at=e["at"])
        return state

    def pending(self) -> list[dict]:
        return [i for i in self.items().values() if i["status"] == "pending"]

    def decide(self, run_id: str, approve: bool, note: str = "") -> dict:
        item = self.items().get(run_id)
        if item is None:
            raise KeyError(f"no queued run {run_id!r}")
        if item["status"] != "pending":
            raise ValueError(f"run {run_id!r} is already {item['status']}")
        self._append({"event": "approved" if approve else "rejected", "run_id": run_id, "note": note})
        return self.items()[run_id]
