"""The only module that talks to Gemini.

Every call goes through the global RateLimiter first. Errors are classified:
  429                   → cooldown + QuotaExceeded (HTTP 429 with Retry-After), no retry
  5xx / network         → one quick retry, then LLMUnavailable (HTTP 503)
  invalid JSON / schema → one retry, then LLMBadOutput (HTTP 502)
  other 4xx             → raised as-is (configuration problem, logged as a 500)
Nothing here sleeps for more than a couple of seconds.
"""
import asyncio
import json
import logging
import re
from functools import lru_cache
from typing import TypeVar

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.extraction.quota import LLMBadOutput, LLMUnavailable, QuotaExceeded, RateLimiter

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

_SERVER_RETRY_DELAY_S = 1.5
_DEFAULT_429_COOLDOWN_S = 60


@lru_cache(maxsize=1)
def get_client() -> genai.Client:
    return genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=settings.gemini_timeout_s * 1000),
    )


@lru_cache(maxsize=1)
def get_limiter() -> RateLimiter:
    return RateLimiter(settings.gemini_rpm, settings.gemini_rpd, settings.gemini_explain_ceiling)


def retry_after_from_error(err: Exception) -> int:
    """Read google.rpc.RetryInfo.retryDelay (e.g. "34s") from a Gemini 429, if present."""
    details = getattr(err, "details", None)
    try:
        for d in (details or {}).get("error", {}).get("details", []):
            delay = d.get("retryDelay")
            if delay:
                match = re.match(r"(\d+(?:\.\d+)?)s", str(delay))
                if match:
                    return max(1, int(float(match.group(1)) + 0.999))
    except AttributeError:
        pass
    return _DEFAULT_429_COOLDOWN_S


async def generate_text(prompt: str, kind: str = "parse", *,
                        temperature: float = 0.1, json_mode: bool = False) -> str:
    limiter = get_limiter()
    config = types.GenerateContentConfig(
        temperature=temperature,
        response_mime_type="application/json" if json_mode else None,
    )

    for attempt in range(2):
        limiter.acquire(kind)  # raises QuotaExceeded before any network call
        try:
            response = await asyncio.to_thread(
                get_client().models.generate_content,
                model=settings.gemini_model,
                contents=prompt,
                config=config,
            )
            return (response.text or "").strip()
        except genai_errors.APIError as e:
            if e.code == 429:
                wait = retry_after_from_error(e)
                limiter.cooldown(wait)
                raise QuotaExceeded(wait, "gemini_rate_limited") from e
            if e.code and e.code >= 500 and attempt == 0:
                logger.warning("Gemini %s, retrying once", e.code)
                await asyncio.sleep(_SERVER_RETRY_DELAY_S)
                continue
            if e.code and e.code >= 500:
                raise LLMUnavailable(f"Gemini returned {e.code}") from e
            raise
        except (TimeoutError, ConnectionError, OSError) as e:
            if attempt == 0:
                logger.warning("Gemini network error (%s), retrying once", type(e).__name__)
                await asyncio.sleep(_SERVER_RETRY_DELAY_S)
                continue
            raise LLMUnavailable(f"Gemini unreachable: {type(e).__name__}") from e

    raise LLMUnavailable("Gemini unavailable")  # unreachable; keeps type checkers happy


def _parse_json(raw: str) -> dict:
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(raw)


async def generate_model(prompt: str, model_cls: type[M], kind: str = "parse") -> M:
    """JSON-mode call validated against model_cls; one retry on bad output."""
    last_error: Exception | None = None
    for attempt in range(2):
        raw = await generate_text(prompt, kind, json_mode=True)
        try:
            return model_cls(**_parse_json(raw))
        except (json.JSONDecodeError, ValidationError, TypeError, IndexError) as e:
            last_error = e
            logger.warning("Gemini output failed %s validation (attempt %d)",
                           model_cls.__name__, attempt + 1)
    raise LLMBadOutput(f"Gemini output did not match {model_cls.__name__}") from last_error
