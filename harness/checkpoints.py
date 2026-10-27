"""Checkpoints: the run state saved as JSON after every completed stage.

    .omnigent-logs/runs/<run_id>/checkpoint-01-parsed.json, -02-drafted.json, ...

`latest()` returns the last saved state, so `python -m harness run --resume-run <run_id>` restarts
after the last good stage (graph writes are idempotent upserts, so repeating one is safe).
Checkpoints hold parsed resume data: they live under .omnigent-logs/, which git ignores.
"""
import json
from pathlib import Path

STAGES = ("parsed", "drafted", "verified", "revised", "final")


class Checkpoints:
    def __init__(self, run_dir: Path):
        self.dir = Path(run_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, stage: str, state: dict) -> Path:
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        path = self.dir / f"checkpoint-{STAGES.index(stage) + 1:02d}-{stage}.json"
        path.write_text(json.dumps({"stage": stage, "state": state}, indent=1), encoding="utf-8")
        return path

    def latest(self) -> tuple[str, dict] | None:
        found = sorted(self.dir.glob("checkpoint-*.json"))
        if not found:
            return None
        data = json.loads(found[-1].read_text(encoding="utf-8"))
        return data["stage"], data["state"]

    def done(self, stage: str) -> bool:
        latest = self.latest()
        return latest is not None and STAGES.index(latest[0]) >= STAGES.index(stage)
