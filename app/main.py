from fastapi import FastAPI
from app.api.routes import router
from app.db.neo4j import neo4j_client

app = FastAPI(title="Nextnode API")
app.include_router(router, prefix="/api/v1")

@app.on_event("shutdown")
def shutdown():
    neo4j_client.close()

@app.get("/health")
def health():
    return {"status": "ok"}