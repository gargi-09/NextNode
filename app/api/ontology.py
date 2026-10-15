import asyncio

from fastapi import APIRouter, Query
from app.api.errors import handle
from app.graph import catalog
from app.graph.ontology import get_ontology
from app.graph.payloads import domain_node_id, skill_node_id

router = APIRouter(prefix="/ontology", tags=["ontology"])


@router.get("/graph")
def ontology_graph():
    """The whole canonical ontology as react-force-graph data, served from the
    version-controlled ontology file — zero database reads."""
    o = get_ontology()
    nodes = [
        {"id": skill_node_id(s.name), "label": s.name, "type": "skill",
         "category": s.category, "level": s.level, "val": 2.0}
        for s in sorted(o.skills.values(), key=lambda s: s.key)
    ] + [
        {"id": domain_node_id(key), "label": key, "type": "domain", "val": 4.0}
        for key in sorted(o.domains)
    ]
    links = [
        {"source": skill_node_id(p.source), "target": skill_node_id(p.target),
         "type": "PREREQUISITE_OF", "strength": p.strength}
        for p in o.prerequisites
    ] + [
        {"source": skill_node_id(s.name), "target": domain_node_id(d), "type": "BELONGS_TO"}
        for s in o.skills.values() for d in s.domains
    ]
    return {"version": o.version, "nodes": nodes, "links": links}


@router.get("/unmapped")
async def unmapped_terms(limit: int = Query(100, ge=1, le=500)):
    """Review queue: skill terms the ontology couldn't resolve, most frequent first.
    Add them as skills or aliases in ontology/skills.json, then regenerate the seed."""
    return await handle(asyncio.to_thread(catalog.unmapped_terms, limit), "Unmapped term listing")
