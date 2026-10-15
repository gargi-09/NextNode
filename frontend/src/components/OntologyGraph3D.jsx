import { useEffect, useMemo, useRef } from "react";
import ForceGraph3D from "react-force-graph-3d";
import { useIsDark, useWidth } from "../hooks/useLayout";
import { CATEGORY_COLORS, escapeHtml } from "./graphColors";

/** The whole canonical ontology (/ontology/graph) as a 3D force graph. */
export default function OntologyGraph3D({ graph, highlight, height = 620 }) {
  const dark = useIsDark();
  const [ref, width] = useWidth();
  const fgRef = useRef(null);
  const fitted = useRef(false);
  useEffect(() => {
    fitted.current = false;
    // fallback: frame callbacks are throttled in background tabs, so the engine-stop fit may not fire
    const t = setTimeout(() => fgRef.current?.zoomToFit(600, 20), 2500);
    return () => clearTimeout(t);
  }, [graph, width]);

  const data = useMemo(() => ({
    nodes: graph.nodes.map(n => ({ ...n })),
    links: graph.links.map(l => ({ ...l })),
  }), [graph]);

  const color = n => {
    if (highlight && n.label.toLowerCase().includes(highlight.toLowerCase())) return "#FACC15";
    return CATEGORY_COLORS[n.type === "domain" ? "domain" : n.category] || "#9CA3AF";
  };

  return (
    <div ref={ref} className="rounded-xl overflow-hidden border border-gray-100 dark:border-gray-800">
      {width > 0 && (
        <ForceGraph3D
          ref={fgRef}
          graphData={data}
          cooldownTicks={150}
          onEngineStop={() => {
            if (!fitted.current) { fgRef.current?.zoomToFit(600, 20); fitted.current = true; }
          }}
          width={width}
          height={height}
          backgroundColor={dark ? "#030712" : "#F3F4F6"}
          nodeLabel={n => `<b>${escapeHtml(n.label)}</b><br/>${escapeHtml(n.type === "domain" ? "domain" : `${n.category} · ${n.level}`)}`}
          nodeColor={color}
          nodeVal={n => n.val}
          nodeOpacity={0.95}
          linkColor={l => (l.type === "PREREQUISITE_OF" ? "#F59E0B" : dark ? "#374151" : "#D1D5DB")}
          linkOpacity={0.6}
          linkWidth={l => (l.type === "PREREQUISITE_OF" ? 1 : 0.3)}
          linkDirectionalArrowLength={l => (l.type === "PREREQUISITE_OF" ? 3 : 0)}
          linkDirectionalArrowRelPos={1}
          linkDirectionalParticles={l => (l.type === "PREREQUISITE_OF" ? 1 : 0)}
          linkDirectionalParticleWidth={1.2}
        />
      )}
    </div>
  );
}
