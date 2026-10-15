import { useEffect, useMemo, useRef } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { useIsDark, useWidth } from "../hooks/useLayout";
import { LINK_COLORS, STATUS_COLORS, TYPE_COLORS, escapeHtml } from "./graphColors";


const LEGEND = [
  ["You", TYPE_COLORS.user], ["Job", TYPE_COLORS.job], ["Domain", TYPE_COLORS.domain],
  ["Met", STATUS_COLORS.met], ["Partial", STATUS_COLORS.partial], ["Missing", STATUS_COLORS.missing],
  ["Owned prerequisite", STATUS_COLORS.owned], ["Intermediate step", STATUS_COLORS.prerequisite],
];
const LINK_LEGEND = [
  ["HAS_SKILL", LINK_COLORS.HAS_SKILL], ["REQUIRES_SKILL", LINK_COLORS.REQUIRES_SKILL],
  ["PREREQUISITE_OF (dashed)", LINK_COLORS.PREREQUISITE_OF],
];


function nodeColor(n) {
  return n.type === "skill" ? STATUS_COLORS[n.status] || "#94A3B8" : TYPE_COLORS[n.type];
}

function tooltip(n) {
  const lines = [`<b>${escapeHtml(n.label)}</b>`, escapeHtml(n.type)];
  if (n.status) lines.push(`status: ${escapeHtml(n.status)}`);
  if (n.importance) lines.push(`importance: ${escapeHtml(n.importance)}`);
  if (n.proficiency != null) lines.push(`you: ${Math.round(n.proficiency * 100)}%`);
  if (n.required_weight != null) lines.push(`required: ${Math.round(n.required_weight * 100)}%`);
  return lines.join("<br/>");
}

/** The `graph` payload of /analyze, drawn as-is with react-force-graph. */
export default function AnalysisGraph({ graph, height = 460 }) {
  const dark = useIsDark();
  const [containerRef, width] = useWidth();
  const fgRef = useRef(null);
  const fitted = useRef(false);

  // force-graph mutates nodes/links (adds x, y, and resolves source/target) — give it copies
  const data = useMemo(() => ({
    nodes: graph.nodes.map(n => ({ ...n })),
    links: graph.links.map(l => ({ ...l })),
  }), [graph]);

  // spread nodes out: the default forces pack ~15 labelled nodes too tightly
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg) return;
    fg.d3Force("charge")?.strength(-220);
    fg.d3Force("link")?.distance(55);
    fg.d3ReheatSimulation();
  }, [data, width]);

  // zoom to fit once per new analysis; the timer covers tabs where frame callbacks
  // are throttled and the engine-stop event arrives late or not at all
  useEffect(() => {
    fitted.current = false;
    const t = setTimeout(() => fgRef.current?.zoomToFit(400, 40), 1500);
    return () => clearTimeout(t);
  }, [graph, width]);

  return (
    <div>
      <div ref={containerRef} className="rounded-xl overflow-hidden border border-gray-100 dark:border-gray-800">
        {width > 0 && (
          <ForceGraph2D
            ref={fgRef}
            graphData={data}
            width={width}
            height={height}
            backgroundColor={dark ? "#030712" : "#F9FAFB"}
            nodeLabel={tooltip}
            nodeVal={n => n.val}
            nodeRelSize={4}
            linkColor={l => LINK_COLORS[l.type]}
            linkWidth={l => (l.type === "PREREQUISITE_OF" ? 1.5 : 1)}
            linkLineDash={l => (l.type === "PREREQUISITE_OF" ? [4, 3] : null)}
            linkDirectionalArrowLength={4}
            linkDirectionalArrowRelPos={1}
            cooldownTicks={120}
            onEngineStop={() => {
              if (!fitted.current) { fgRef.current?.zoomToFit(400, 40); fitted.current = true; }
            }}
            nodeCanvasObject={(node, ctx, scale) => {
              const r = Math.sqrt(node.val || 1) * 4;
              ctx.beginPath();
              ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
              ctx.fillStyle = nodeColor(node);
              ctx.fill();
              const fontSize = Math.max(10 / scale, 2.5);
              ctx.font = `${fontSize}px sans-serif`;
              ctx.textAlign = "center";
              ctx.textBaseline = "top";
              ctx.fillStyle = dark ? "#E5E7EB" : "#1F2937";
              ctx.fillText(node.label, node.x, node.y + r + 1.5);
            }}
            nodePointerAreaPaint={(node, color, ctx) => {
              ctx.fillStyle = color;
              ctx.beginPath();
              ctx.arc(node.x, node.y, Math.sqrt(node.val || 1) * 4 + 2, 0, 2 * Math.PI);
              ctx.fill();
            }}
          />
        )}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1.5 mt-3 text-xs text-gray-500 dark:text-gray-400">
        {LEGEND.map(([label, color]) => (
          <span key={label} className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full" style={{ background: color }} />{label}
          </span>
        ))}
        {LINK_LEGEND.map(([label, color]) => (
          <span key={label} className="flex items-center gap-1.5">
            <span className="w-4 h-0.5" style={{ background: color }} />{label}
          </span>
        ))}
      </div>
    </div>
  );
}
