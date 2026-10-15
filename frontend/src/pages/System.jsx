import { getGraphHealth } from "../api/client";
import { useApi } from "../hooks/useApi";
import { loadOntology } from "../hooks/useLayout";
import { Badge, Button, Card, ErrorNote, JsonToggle, Loading } from "../components/ui";

// Neo4j's published Aura Free limits (conservative figures, TDD-phase1-2 §1)
const AURA_FREE = { nodes: 50000, relationships: 175000 };

function Meter({ label, used, limit, unit = "" }) {
  const pct = limit ? Math.min(100, (used / limit) * 100) : 0;
  const color = pct > 80 ? "bg-red-500" : pct > 60 ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-gray-600 dark:text-gray-300">{label}</span>
        <span className="tabular-nums text-gray-500 dark:text-gray-400">
          {used.toLocaleString()} / {limit.toLocaleString()}{unit}
        </span>
      </div>
      <div className="h-2 rounded-full bg-gray-100 dark:bg-gray-800">
        <div className={`h-2 rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function System() {
  const health = useApi(getGraphHealth);
  const ontology = useApi(loadOntology);
  const h = health.data;
  const ontologySkills = ontology.data?.nodes.filter(n => n.type === "skill").length;
  const seedOutdated = h?.graph === "ok" && ontologySkills && h.canonical_skills < ontologySkills;

  return (
    <div className="space-y-6">
      <Card title="Free-tier status" subtitle="Live from GET /health/graph"
        actions={<Button variant="secondary" onClick={health.reload} pending={health.loading}>Refresh</Button>}>
        <ErrorNote error={health.error} />
        {health.loading && !h ? <Loading label="Checking Neo4j (up to ~10s if unreachable)…" /> : h && (
          <div className="space-y-6">
            <div className="flex flex-wrap gap-2">
              <Badge tone={h.graph === "ok" ? "met" : "missing"}>Neo4j: {h.graph}</Badge>
              <Badge tone={h.breaker === "closed" ? "met" : "warning"}>circuit breaker: {h.breaker}</Badge>
            </div>

            {h.graph !== "ok" && (
              <div className="rounded-lg border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/40 p-3 text-sm text-red-700 dark:text-red-300">
                {h.hint}
                {h.retry_after && <div className="text-xs mt-1">Retry after {h.retry_after}s.</div>}
              </div>
            )}

            {seedOutdated && (
              <div className="rounded-lg border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/40 p-3 text-sm text-amber-800 dark:text-amber-200">
                The graph has {h.canonical_skills} canonical skills but the ontology defines {ontologySkills}.
                Run <code>cypher/constraints.cypher</code> then <code>cypher/seed.cypher</code> in the Aura console
                so prerequisite chains show up in analyses.
              </div>
            )}

            {h.graph === "ok" && (
              <div className="grid md:grid-cols-2 gap-6">
                <div className="space-y-3">
                  <p className="text-xs font-semibold text-gray-700 dark:text-gray-200">Neo4j Aura Free capacity</p>
                  <Meter label="Nodes" used={h.nodes} limit={AURA_FREE.nodes} />
                  <Meter label="Relationships (the binding limit)" used={h.relationships} limit={AURA_FREE.relationships} />
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    Canonical skills in graph: {h.canonical_skills}{ontologySkills ? ` / ${ontologySkills} in ontology` : ""}
                  </p>
                </div>
                <GeminiUsage g={h.gemini} />
              </div>
            )}
            {h.graph !== "ok" && h.gemini && <GeminiUsage g={h.gemini} />}
          </div>
        )}
      </Card>

      <Card title="How the pieces connect">
        <ul className="text-sm text-gray-600 dark:text-gray-300 space-y-1.5 list-disc pl-5">
          <li>This UI (Vite, :5173) → proxy → FastAPI (:8000) → Neo4j Aura and Gemini.</li>
          <li>Gemini is only called for resume / job text parsing, pivot role profiles and optional explanations; everything else is graph queries plus fixed rules.</li>
          <li>The Gemini counter is per API process and resets at midnight Pacific; restarting uvicorn resets it too.</li>
        </ul>
      </Card>
      {h && <JsonToggle data={h} label="Raw /health/graph response" />}
    </div>
  );
}

function GeminiUsage({ g }) {
  if (!g) return null;
  return (
    <div className="space-y-3">
      <p className="text-xs font-semibold text-gray-700 dark:text-gray-200">Gemini budget (this API process)</p>
      <Meter label="Requests today (Pacific)" used={g.rpd_used} limit={g.rpd_limit} />
      <Meter label="Requests this minute" used={g.rpm_used} limit={g.rpm_limit} />
      {g.cooldown_s > 0 && <Badge tone="warning">Gemini cooldown: {g.cooldown_s}s</Badge>}
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Explanations stop at 80% of the daily budget so parsing keeps working.
      </p>
    </div>
  );
}
