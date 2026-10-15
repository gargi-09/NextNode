import logging

from fastapi import HTTPException
from app.core.errors import ConflictError, NotFoundError
from app.db.neo4j import GraphUnavailableError
from app.extraction.quota import LLMBadOutput, LLMUnavailable, QuotaExceeded

logger = logging.getLogger(__name__)

LLM_RETRY_AFTER_S = 30


async def handle(coro, action: str):
    """Run a service call and translate failures into HTTP responses.

    429 / 503 responses carry a Retry-After header and a machine-readable `code`,
    so clients back off instead of the server sleeping.
    """
    try:
        return await coro
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except QuotaExceeded as e:
        raise HTTPException(
            status_code=429,
            headers={"Retry-After": str(e.retry_after)},
            detail={"code": "LLM_QUOTA", "reason": e.reason, "retry_after": e.retry_after,
                    "message": "The Gemini request budget is used up for now. Retry later."},
        )
    except GraphUnavailableError as e:
        logger.warning("%s failed: graph unavailable", action)
        raise HTTPException(
            status_code=503,
            headers={"Retry-After": str(e.retry_after)},
            detail={"code": "GRAPH_UNAVAILABLE", "retry_after": e.retry_after, "hint": e.hint},
        )
    except LLMUnavailable:
        logger.warning("%s failed: Gemini unavailable", action)
        raise HTTPException(
            status_code=503,
            headers={"Retry-After": str(LLM_RETRY_AFTER_S)},
            detail={"code": "LLM_UNAVAILABLE", "retry_after": LLM_RETRY_AFTER_S},
        )
    except LLMBadOutput:
        logger.warning("%s failed: Gemini returned invalid output", action)
        raise HTTPException(status_code=502, detail={"code": "LLM_BAD_OUTPUT"})
    except Exception:
        # details go to the server log, not to the client
        logger.exception("%s failed", action)
        raise HTTPException(status_code=500, detail=f"{action} failed")
