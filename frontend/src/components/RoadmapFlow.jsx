import { useMemo } from "react";
import { Background, Controls, Handle, MarkerType, Position, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useIsDark } from "../hooks/useLayout";
import { Badge } from "./ui";

const PRIORITY_BORDER = {
  high: "border-red-500", medium: "border-amber-500", low: "border-gray-300 dark:border-gray-600",
};

function StepNode({ data }) {
  return (
    <div className={`w-64 rounded-xl border-2 ${PRIORITY_BORDER[data.priority]} bg-white dark:bg-gray-900 px-3 py-2.5 shadow-sm`}>
      <Handle type="target" position={Position.Top} id="top" />
      <Handle type="target" position={Position.Left} id="left" />
      <div className="flex items-center gap-2 mb-1">
        <span className="w-5 h-5 rounded-full bg-gray-100 dark:bg-gray-800 text-[11px] font-bold flex items-center justify-center text-gray-600 dark:text-gray-300">
          {data.rank}
        </span>
        <span className="text-sm font-semibold text-gray-900 dark:text-white truncate">{data.label}</span>
      </div>
      <div className="flex gap-1.5 mb-1.5">
        <Badge tone={data.status}>{data.status}</Badge>
        <Badge tone={data.importance}>{data.importance?.replace(/_/g, " ")}</Badge>
        <Badge tone="neutral">{data.priority}</Badge>
      </div>
      <p className="text-[11px] leading-snug text-gray-500 dark:text-gray-400">{data.reason}</p>
      <Handle type="source" position={Position.Bottom} id="bottom" />
    </div>
  );
}

function OwnedNode({ data }) {
  return (
    <div className="w-48 rounded-xl border border-teal-500 bg-teal-50 dark:bg-teal-950/60 px-3 py-2">
      <div className="text-xs text-teal-700 dark:text-teal-300">you already have</div>
      <div className="text-sm font-semibold text-teal-900 dark:text-teal-100">{data.label}</div>
      {data.proficiency != null && (
        <div className="text-[11px] text-teal-700 dark:text-teal-300">{Math.round(data.proficiency * 100)}%</div>
      )}
      <Handle type="source" position={Position.Right} id="right" />
    </div>
  );
}

const NODE_TYPES = { roadmapStep: StepNode, ownedSkill: OwnedNode };

/** The `roadmap_flow` payload of /analyze, rendered with @xyflow/react. */
export default function RoadmapFlow({ flow }) {
  const dark = useIsDark();

  const { nodes, edges } = useMemo(() => ({
    nodes: flow.nodes.map(n => ({ ...n, draggable: true })),
    edges: flow.edges.map(e => {
      const unlocks = e.data.kind === "unlocks";
      return {
        ...e,
        // the backend sends positions and ids; handles depend on which side the edge attaches
        sourceHandle: unlocks ? "right" : "bottom",
        targetHandle: unlocks ? "left" : "top",
        markerEnd: { type: MarkerType.ArrowClosed },
        style: unlocks
          ? { stroke: "#0D9488", strokeDasharray: "5 4" }
          : { stroke: dark ? "#6B7280" : "#9CA3AF" },
        labelStyle: { fontSize: 10 },
      };
    }),
  }), [flow, dark]);

  const steps = flow.nodes.filter(n => n.type === "roadmapStep").length;
  const height = Math.min(Math.max(360, steps * 140 + 80), 900);

  if (!flow.nodes.length) {
    return <p className="text-sm text-gray-500 py-6 text-center">No gaps — nothing to learn for this job.</p>;
  }

  return (
    <div style={{ height }} className="rounded-xl overflow-hidden border border-gray-100 dark:border-gray-800">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        colorMode={dark ? "dark" : "light"}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        proOptions={{ hideAttribution: true }}
        nodesConnectable={false}
      >
        <Background gap={20} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
