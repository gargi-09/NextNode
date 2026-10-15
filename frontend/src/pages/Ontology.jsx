import { useState } from "react";
import { getUnmapped } from "../api/client";
import { useApi } from "../hooks/useApi";
import { loadOntology } from "../hooks/useLayout";
import OntologyGraph3D from "../components/OntologyGraph3D";
import { CATEGORY_COLORS } from "../components/graphColors";
import { Badge, Card, Empty, ErrorNote, Input, Loading } from "../components/ui";

export default function Ontology() {
  const ontology = useApi(loadOntology);
  const unmapped = useApi(getUnmapped);
  const [search, setSearch] = useState("");

  const g = ontology.data;
  const skills = g?.nodes.filter(n => n.type === "skill") ?? [];
  const prereqs = g?.links.filter(l => l.type === "PREREQUISITE_OF").length ?? 0;
  const matches = search ? skills.filter(s => s.label.toLowerCase().includes(search.toLowerCase())) : [];

  return (
    <div className="space-y-6">
      <Card title="Skill ontology"
        subtitle="The canonical skills, prerequisite chains (orange, animated) and domains from ontology/skills.json. This is the vocabulary every resume and job is resolved against. Drag to rotate, scroll to zoom."
        actions={g && <Badge tone="neutral">v{g.version}</Badge>}>
        <ErrorNote error={ontology.error} />
        {ontology.loading && !g ? <Loading /> : g && (
          <>
            <div className="flex flex-wrap items-center gap-3 mb-3">
              <Badge tone="core">{skills.length} skills</Badge>
              <Badge tone="preferred">{prereqs} prerequisite edges</Badge>
              <div className="w-56"><Input value={search} onChange={e => setSearch(e.target.value)} placeholder="Highlight a skill…" /></div>
              {search && <span className="text-xs text-gray-500">{matches.length} match{matches.length === 1 ? "" : "es"}</span>}
            </div>
            <OntologyGraph3D graph={g} highlight={search} />
            <div className="flex flex-wrap gap-4 mt-3 text-xs text-gray-500 dark:text-gray-400">
              {Object.entries(CATEGORY_COLORS).map(([cat, color]) => (
                <span key={cat} className="flex items-center gap-1.5">
                  <span className="w-2.5 h-2.5 rounded-full" style={{ background: color }} />{cat.replace("_", " ")}
                </span>
              ))}
              <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-yellow-400" />search match</span>
            </div>
          </>
        )}
      </Card>

      <Card title="Review queue: unmapped terms"
        subtitle="Skill names from resumes and jobs that matched no canonical skill or alias. Add the useful ones to ontology/skills.json, run scripts/build_seed.py, and re-run cypher/seed.cypher.">
        <ErrorNote error={unmapped.error} />
        {unmapped.loading && !unmapped.data ? <Loading /> : !unmapped.data?.length ? (
          <Empty>Nothing unmapped yet: every ingested skill resolved to the ontology.</Empty>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 dark:text-gray-400">
                <th className="pb-2 font-medium">Term</th><th className="pb-2 font-medium">Seen</th>
                <th className="pb-2 font-medium">In users</th><th className="pb-2 font-medium">In jobs</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {unmapped.data.map(t => (
                <tr key={t.term}>
                  <td className="py-2 font-medium text-gray-900 dark:text-white">{t.term}</td>
                  <td className="py-2 tabular-nums">{t.freq}</td>
                  <td className="py-2 tabular-nums">{t.in_users}</td>
                  <td className="py-2 tabular-nums">{t.in_jobs}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
