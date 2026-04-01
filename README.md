# Nextnode

AI-powered skill gap analyzer that thinks in graphs. Paste a resume and job description, get back a personalized fit score and learning roadmap.

## Stack
- FastAPI + Python
- Neo4j AuraDB (knowledge graph)
- Gemini 2.5 Flash (extraction + explanation)
- Google Cloud Run (deployment)

## How it works
1. LLM extracts skills from resume and job description
2. Skills are stored as nodes and relationships in Neo4j
3. Symbolic rule engine traverses the graph to compute gap scores
4. LLM explains the results in plain language

## Setup
```bash
git clone https://github.com/yourusername/nextnode
cd nextnode
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file:
```
NEO4J_URI=your_uri
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
GEMINI_API_KEY=your_key
```

Run locally:
```bash
uvicorn app.main:app --reload
```

## Status
Work in progress.
