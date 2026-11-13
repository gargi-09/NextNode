# The public NextNode API (FastAPI) for Cloud Run. Public mode is on by default in this image:
# only the stateless analyze flow, the read-only ontology and health are served.
# Secrets (Neo4j, Gemini) come from the environment at run time, never from the image.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PUBLIC_MODE=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ app/
COPY ontology/ ontology/

RUN useradd --system --no-create-home nextnode
USER nextnode

# Cloud Run sets $PORT. No --proxy-headers: the app reads the client IP from the last
# X-Forwarded-For hop itself (app/api/public.py).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
