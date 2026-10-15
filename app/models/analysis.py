"""Typed /analyze response, shaped for direct use by the frontend.

`graph`        → react-force-graph:  <ForceGraph3D graphData={graph} />
                 (nodes[].id, links[].source / links[].target reference node ids)
`roadmap_flow` → @xyflow/react:      <ReactFlow nodes={roadmap_flow.nodes} edges={roadmap_flow.edges} />
                 (every node has a position; custom node types "roadmapStep" / "ownedSkill")
"""
from typing import Literal, Optional

from pydantic import BaseModel

SkillStatus = Literal["met", "partial", "missing"]
Importance = Literal["core", "preferred", "nice_to_have"]


class SkillGap(BaseModel):
    skill: str
    category: Optional[str] = None
    importance: str
    status: SkillStatus
    your_proficiency: float
    required_weight: float
    prereqs_owned: int
    gap_contribution: float


class RoadmapStep(BaseModel):
    rank: int
    skill: str
    priority: Literal["high", "medium", "low"]
    status: SkillStatus
    importance: str
    prereqs_owned: int
    reason: str


# ── react-force-graph ──

class GraphNode(BaseModel):
    id: str                       # "user:<id>" | "job:<id>" | "skill:<name>" | "domain:<key>"
    label: str
    type: Literal["user", "job", "skill", "domain"]
    # skills only: met / partial / missing for required skills; "owned" for a skill the
    # user has that leads to a gap; "prerequisite" for an intermediate step they lack
    status: Optional[Literal["met", "partial", "missing", "owned", "prerequisite"]] = None
    importance: Optional[str] = None
    proficiency: Optional[float] = None
    required_weight: Optional[float] = None
    val: float = 1.0              # react-force-graph node size


class GraphLink(BaseModel):
    source: str
    target: str
    type: Literal["HAS_SKILL", "REQUIRES_SKILL", "PREREQUISITE_OF", "TARGETS", "IN_DOMAIN"]
    proficiency: Optional[float] = None   # HAS_SKILL
    importance: Optional[str] = None      # REQUIRES_SKILL
    weight: Optional[float] = None        # REQUIRES_SKILL
    strength: Optional[float] = None      # PREREQUISITE_OF


class GraphPayload(BaseModel):
    nodes: list[GraphNode]
    links: list[GraphLink]


# ── @xyflow/react ──

class FlowPosition(BaseModel):
    x: float
    y: float


class FlowNodeData(BaseModel):
    label: str
    rank: Optional[int] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    importance: Optional[str] = None
    reason: Optional[str] = None
    prereqs_owned: Optional[int] = None
    proficiency: Optional[float] = None


class FlowNode(BaseModel):
    id: str
    type: Literal["roadmapStep", "ownedSkill"]
    position: FlowPosition
    data: FlowNodeData


class FlowEdgeData(BaseModel):
    kind: Literal["sequence", "unlocks"]


class FlowEdge(BaseModel):
    id: str
    source: str
    target: str
    type: str = "smoothstep"
    label: Optional[str] = None
    animated: bool = False
    data: FlowEdgeData


class FlowPayload(BaseModel):
    nodes: list[FlowNode]
    edges: list[FlowEdge]


class AnalysisResponse(BaseModel):
    user_id: str
    job_id: str
    user_name: Optional[str] = None
    job_title: Optional[str] = None
    company: Optional[str] = None
    scoring_version: str
    fit_score: float
    gap_score: float
    domain_bonus: float
    skill_breakdown: list[SkillGap]
    roadmap: list[RoadmapStep]
    graph: GraphPayload
    roadmap_flow: FlowPayload
    explanation: Optional[str] = None
    explanation_status: Literal["generated", "not_requested", "skipped_quota", "failed"]
