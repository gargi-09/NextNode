"""NextNode MCP server: the graph tools the Omnigent agents call (docs/TDD-phase3-omnigent.md §3).

Seven tools, each a thin wrapper over app/services/*, so behavior matches the REST API:
strict ontology resolution, single-transaction writes, the Neo4j circuit breaker.
No tool calls an LLM and none deletes data.

    python -m app.mcp_server                                   # stdio (MCP clients that spawn it)
    python -m app.mcp_server --transport http --port 8765      # Streamable HTTP for Omnigent

HTTP mode binds loopback only, serves MCP at /mcp, `GET /health` without auth, and
requires `Authorization: Bearer $NEXTNODE_MCP_TOKEN` on everything else. The token
comes from the environment (scripts/omnigent_up.* generate one per run); it is never
logged. Tool arguments carry resume data and are never logged either.
"""
import argparse
import asyncio
import hmac
import json
import logging
import os
import sys

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.db.neo4j import GraphUnavailableError
from app.graph import catalog
from app.graph.ontology import get_ontology
from app.models.schemas import JobCreate, RequiredSkillInput, SkillInput
from app.services import analysis, jobs, users

logger = logging.getLogger("nextnode.mcp")

TOKEN_ENV = "NEXTNODE_MCP_TOKEN"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
DEFAULT_PORT = 8765
# Graph and roadmap layouts are for the UI; agents only need the numbers.
GAP_FIELDS_DROPPED = {"graph", "roadmap_flow", "explanation", "explanation_status"}

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)

mcp = FastMCP(
    "nextnode",
    instructions=(
        "Deterministic tools over the NextNode career graph. Scores, priorities and "
        "prerequisites come from fixed rules over Neo4j; copy them, never recompute them. "
        "Skill names are resolved strictly against a canonical ontology: unknown names are "
        "returned in `unmapped_skills` and never become skills. A tool error ends the step."
    ),
    stateless_http=True,     # no sessions: any request can be retried
    json_response=True,
    streamable_http_path="/mcp",
    # Host/Origin checks against DNS rebinding (the server only listens on loopback anyway)
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    ),
)


def _fail(code: str, message: str, **extra) -> ToolError:
    """A tool error whose text is machine-readable JSON with NextNode's error codes."""
    return ToolError(json.dumps({"code": code, "message": message, **extra}))


async def _run(coro, action: str):
    try:
        return await coro
    except NotFoundError as e:
        raise _fail("NOT_FOUND", str(e))
    except ConflictError as e:
        raise _fail("CONFLICT", str(e))
    except UnprocessableError as e:
        raise _fail("UNPROCESSABLE", str(e))
    except ValidationError as e:
        raise _fail("INVALID_INPUT", "Invalid arguments",
                    errors=e.errors(include_url=False, include_input=False, include_context=False))
    except GraphUnavailableError as e:
        raise _fail("GRAPH_UNAVAILABLE", "The graph database is unavailable.",
                    retry_after=e.retry_after, hint=e.hint)
    except ToolError:
        raise
    except Exception:
        logger.exception("%s failed", action)   # details stay in the server log
        raise _fail("INTERNAL", f"{action} failed")


# ─────────────────────────── read tools ───────────────────────────

@mcp.tool(annotations=READ)
async def resolve_skills(
    names: list[str] = Field(description="Raw skill names, e.g. ['k8s', 'torch', 'Rust']", max_length=200),
) -> dict:
    """Map raw skill names to canonical ontology skills (exact name or alias, no fuzzy
    matching). Names that match nothing are listed in `unmapped`. No database access."""
    return get_ontology().resolve_names(names)


@mcp.tool(annotations=READ)
async def get_user_profile(user_id: str = Field(min_length=1)) -> dict:
    """A user with their canonical skills (proficiency 0-1, source) and unmapped skill names."""
    return await _run(users.get_user(user_id), "get_user_profile")


@mcp.tool(annotations=READ)
async def get_job_posting(job_id: str = Field(min_length=1)) -> dict:
    """A job posting with its required skills (importance core/preferred/nice_to_have,
    expected weight 0-1), domains and unmapped skill names."""
    return await _run(jobs.get_job(job_id), "get_job_posting")


@mcp.tool(annotations=READ)
async def calculate_skill_gap(user_id: str = Field(min_length=1), job_id: str = Field(min_length=1)) -> dict:
    """Deterministic fit analysis: fit_score, gap_score, domain_bonus, per-skill breakdown
    and a prioritized roadmap with reasons. Copy these values; never recompute them."""
    result = await _run(analysis.analyze_gap(user_id, job_id, explain=False), "calculate_skill_gap")
    return result.model_dump(exclude=GAP_FIELDS_DROPPED)


@mcp.tool(annotations=READ)
async def get_skill_prerequisites(skill: str = Field(min_length=1, description="Skill name or alias")) -> dict:
    """Skills that lead to `skill` through prerequisite chains (up to 3 hops), nearest first.
    `strength` is the product of edge strengths along the path (1 = essential)."""
    canonical = get_ontology().resolve(skill)
    if canonical is None:
        raise _fail("NOT_FOUND", f"'{skill}' is not a canonical skill or alias in the ontology")
    rows = await _run(asyncio.to_thread(catalog.skill_prerequisites, canonical.name),
                      "get_skill_prerequisites")
    return {"skill": canonical.name, "prerequisites": rows}


# ─────────────────────────── write tools ───────────────────────────

@mcp.tool(annotations=WRITE)
async def upsert_user_skills(
    user_id: str = Field(min_length=1),
    skills: list[SkillInput] = Field(description="Full skill list: [{name, proficiency 0-1}]", max_length=200),
    name: str | None = Field(default=None, description="Display name, used only when creating the user"),
    target_domain: str | None = Field(default=None, description="nlp | ml_infrastructure | computer_vision | data_engineering"),
) -> dict:
    """Create the user if missing, then REPLACE their whole skill list. Names are resolved
    against the ontology; unknown ones are returned in `unmapped_skills`, never stored as skills."""
    return await _run(users.upsert_skills(user_id, skills, name, target_domain), "upsert_user_skills")


@mcp.tool(annotations=WRITE)
async def upsert_job_posting(
    job_id: str = Field(min_length=1),
    title: str = Field(min_length=1),
    company: str | None = None,
    seniority: str | None = Field(default=None, description="junior | mid | senior | staff"),
    core_skills: list[RequiredSkillInput] = Field(default_factory=list, max_length=100),
    preferred_skills: list[RequiredSkillInput] = Field(default_factory=list, max_length=100),
    nice_to_have_skills: list[RequiredSkillInput] = Field(default_factory=list, max_length=100),
    domains: list[str] = Field(default_factory=list, max_length=10),
) -> dict:
    """Create the job or fully REPLACE its fields and requirements. Each skill is
    {name, weight 0-1 = the proficiency the role expects}. Unknown skills are returned
    in `unmapped_skills`, unknown domains in `unmapped_domains`."""
    async def write():
        request = JobCreate(id=job_id, title=title, company=company, seniority=seniority,
                            core_skills=core_skills, preferred_skills=preferred_skills,
                            nice_to_have_skills=nice_to_have_skills, domains=domains)
        return await jobs.upsert_job(request)
    return await _run(write(), "upsert_job_posting")


# ─────────────────────────── HTTP transport ───────────────────────────

@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "tools": len(await mcp.list_tools())})


class BearerAuth:
    """ASGI middleware: every HTTP request except GET /health needs the bearer token."""

    def __init__(self, app, token: str):
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/health":
            return await self.app(scope, receive, send)
        given = dict(scope.get("headers") or []).get(b"authorization", b"")
        if not hmac.compare_digest(given, self._expected):
            response = JSONResponse({"code": "UNAUTHORIZED"}, status_code=401,
                                    headers={"WWW-Authenticate": "Bearer"})
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)


def http_app(token: str):
    if not token or len(token) < 16:
        raise ValueError(f"{TOKEN_ENV} must be set to a random string of at least 16 characters")
    return BearerAuth(mcp.streamable_http_app(), token)


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)   # stdout is the stdio channel

    if args.transport == "stdio":
        mcp.run("stdio")
        return
    if args.host not in LOOPBACK:
        parser.error("--host must be a loopback address; use a tunnel for remote access")
    try:
        app = http_app(os.environ.get(TOKEN_ENV, ""))
    except ValueError as e:
        parser.error(str(e))

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
