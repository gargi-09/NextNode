import asyncio
import logging

from app.extraction.explainer import explain_gap
from app.extraction.quota import QuotaExceeded
from app.graph.payloads import build_graph_payload, build_roadmap_flow
from app.graph.reasoner import fetch_gap_inputs
from app.graph.scoring import SCORING_VERSION, score_gap
from app.models.analysis import AnalysisResponse

logger = logging.getLogger(__name__)


async def analyze_gap(user_id: str, job_id: str, explain: bool = True) -> AnalysisResponse:
    inputs = await asyncio.to_thread(fetch_gap_inputs, user_id, job_id)

    analysis = score_gap(
        user_id, job_id,
        inputs["rows"], inputs["shared_domains"], inputs["prereq_coverage"]
    )
    graph = build_graph_payload(
        user_id=user_id, user_name=inputs["user_name"],
        job_id=job_id, job_title=inputs["job_title"],
        skill_breakdown=analysis["skill_breakdown"],
        prereq_paths=inputs["prereq_paths"],
        required_prereq_edges=inputs["required_prereq_edges"],
        user_domains=inputs["user_domains"], job_domains=inputs["job_domains"],
    )
    flow = build_roadmap_flow(analysis["roadmap"], inputs["prereq_paths"])

    explanation, status = None, "not_requested"
    if explain:
        # the deterministic analysis stands on its own if the LLM narration can't run
        try:
            explanation = await explain_gap(
                analysis=analysis,
                user_name=inputs["user_name"],
                job_title=inputs["job_title"],
                company=inputs["company"]
            )
            status = "generated"
        except QuotaExceeded as e:
            logger.info("Explanation skipped: %s", e)
            status = "skipped_quota"
        except Exception:
            logger.exception("Explanation failed for user=%s job=%s", user_id, job_id)
            status = "failed"

    return AnalysisResponse(
        **analysis,
        user_name=inputs["user_name"],
        job_title=inputs["job_title"],
        company=inputs["company"],
        scoring_version=SCORING_VERSION,
        graph=graph,
        roadmap_flow=flow,
        explanation=explanation,
        explanation_status=status,
    )
