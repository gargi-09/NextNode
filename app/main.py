import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import jobs, ontology, public, users
from app.api.routes import router
from app.core.config import settings
from app.core.settings_schema import Settings
from app.db.neo4j import neo4j_client
from app.extraction.llm import get_limiter
from app.services.public import sweep

logger = logging.getLogger(__name__)

# Public mode serves only these; everything else answers 404 even if a router is added later.
PUBLIC_EXACT = {"/health", "/health/graph", "/api/v1/ontology/graph", "/api/v1/ontology/resolve",
                "/demo", "/docs", "/openapi.json"}
PUBLIC_PREFIXES = ("/api/v1/public/", "/demo/", "/docs/")
SWEEP_EVERY_S = 600


def _public_path(path: str) -> bool:
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES)


async def _sweeper(cfg: Settings):
    while True:
        try:
            await sweep(cfg.public_sweep_age_s)
        except Exception:
            logger.warning("Visitor sweep failed; retrying in %ds", SWEEP_EVERY_S)
        await asyncio.sleep(SWEEP_EVERY_S)


def create_app(cfg: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(_sweeper(cfg)) if cfg.public_mode else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        neo4j_client.close()

    app = FastAPI(title="Nextnode API", lifespan=lifespan)
    app.include_router(public.router, prefix="/api/v1")
    app.include_router(ontology.router, prefix="/api/v1")
    if not cfg.public_mode:
        # local use: the full API (writes, deletes, lists, the review queue)
        app.include_router(router, prefix="/api/v1")
        app.include_router(users.router, prefix="/api/v1")
        app.include_router(jobs.router, prefix="/api/v1")

    # The static demo pages (also published with GitHub Pages). Served here too so the
    # sandbox's "Live API" mode can call this API from the same origin.
    docs_dir = Path(__file__).resolve().parents[1] / "docs"
    if docs_dir.is_dir():
        app.mount("/demo", StaticFiles(directory=docs_dir, html=True), name="demo")

    if cfg.public_mode:
        @app.middleware("http")
        async def only_public_paths(request, call_next):
            if not _public_path(request.url.path):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            return await call_next(request)

        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in cfg.public_origins.split(",") if o.strip()],
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
            expose_headers=["Retry-After"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            # any local dev port: Vite moves to 5174+ when 5173 is busy
            allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["Retry-After"],
        )

    @app.get("/health")
    def health():
        """Liveness only — never touches the database or Gemini."""
        return {"status": "ok", "public_mode": cfg.public_mode}

    @app.get("/health/graph")
    async def health_graph():
        """Graph connectivity, breaker state, graph size and today's Gemini usage.
        Returns 503 with a resume hint when Aura is unreachable (e.g. paused)."""
        graph = await asyncio.to_thread(neo4j_client.health)
        body = {**graph, "gemini": get_limiter().usage()}
        return JSONResponse(body, status_code=200 if graph["graph"] == "ok" else 503)

    return app


app = create_app(settings)
