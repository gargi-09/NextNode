import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import jobs, ontology, users
from app.api.routes import router
from app.db.neo4j import neo4j_client
from app.extraction.llm import get_limiter


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    neo4j_client.close()


app = FastAPI(title="Nextnode API", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(ontology.router, prefix="/api/v1")

# The static demo pages (also published with GitHub Pages). Served here too so the
# sandbox's "Live API" mode can call this API from the same origin.
DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"
if DOCS_DIR.is_dir():
    app.mount("/demo", StaticFiles(directory=DOCS_DIR, html=True), name="demo")

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
    return {"status": "ok"}


@app.get("/health/graph")
async def health_graph():
    """Graph connectivity, breaker state, graph size and today's Gemini usage.
    Returns 503 with a resume hint when Aura is unreachable (e.g. paused)."""
    graph = await asyncio.to_thread(neo4j_client.health)
    body = {**graph, "gemini": get_limiter().usage()}
    return JSONResponse(body, status_code=200 if graph["graph"] == "ok" else 503)
