"""NextNode agent harness: the curriculum workflow with production-style recovery.

On top of scripts/curriculum_run.py (same agents, prompts, models and tool allow-lists), the
harness adds: per-tool retry with backoff and circuit breakers, checkpoints with rollback and
resume, an approval queue for low-confidence results, deterministic evaluators in the shape of
the Phoenix evals project, OpenTelemetry traces, model record/replay, and fault injection for
the 20-task eval (eval/). See docs/DESIGN.md.

    python -m harness run --user-id u --job-id j --resume cv.txt --job jd.txt
    python -m harness approvals list
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / ".omnigent-logs" / "runs"

# The harness reuses the runner's agent code (scripts/ is not a package).
_SCRIPTS = str(ROOT / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
