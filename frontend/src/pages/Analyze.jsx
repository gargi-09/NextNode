import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  analyzeGap, analyzePivot, createJob, createUser, listJobs, listUsers, replaceSkills,
} from "../api/client";
import { useAction, useApi } from "../hooks/useApi";
import AnalysisGraph from "../components/AnalysisGraph";
import Explanation from "../components/Explanation";
import FitScore from "../components/FitScore";
import PivotAnalysis from "../components/PivotAnalysis";
import Roadmap from "../components/Roadmap";
import RoadmapFlow from "../components/RoadmapFlow";
import SkillBreakdown from "../components/SkillBreakdown";
import { Badge, Button, Card, ErrorNote, Field, Input, JsonToggle, Select } from "../components/ui";

// Demo data goes through the structured endpoints: zero Gemini requests.
const DEMO_USER = {
  id: "demo_user", name: "Demo Candidate", target_domain: "nlp",
  skills: [
    { name: "Python", proficiency: 0.9 }, { name: "PyTorch", proficiency: 0.4 },
    { name: "Elasticsearch", proficiency: 0.8 }, { name: "SQL", proficiency: 0.7 },
    { name: "Docker", proficiency: 0.5 },
  ],
};
const DEMO_JOB = {
  id: "demo_job", title: "ML Engineer, LLM Platform", company: "Acme AI", seniority: "mid",
  core_skills: [
    { name: "Python", weight: 0.7 }, { name: "PyTorch", weight: 0.7 },
    { name: "LLM fine-tuning", weight: 0.6 }, { name: "HuggingFace Transformers", weight: 0.6 },
  ],
  preferred_skills: [
    { name: "Vector Databases", weight: 0.5 }, { name: "RAG", weight: 0.5 },
  ],
  nice_to_have_skills: [{ name: "Docker", weight: 0.4 }, { name: "k8s", weight: 0.3 }],
  domains: ["nlp", "ml_infrastructure"],
};

const EXPLANATION_STATUS = {
  generated: ["met", "LLM explanation"],
  not_requested: ["neutral", "no explanation requested"],
  skipped_quota: ["warning", "explanation skipped: Gemini budget"],
  failed: ["missing", "explanation failed"],
};

async function loadDemo() {
  const { skills, ...user } = DEMO_USER;
  try { await createUser(user); } catch (e) { if (e.status !== 409) throw e; }
  await replaceSkills(DEMO_USER.id, skills);
  try { await createJob(DEMO_JOB); } catch (e) { if (e.status !== 409) throw e; }
  return true;
}

export default function Analyze() {
  const [params, setParams] = useSearchParams();
  const userId = params.get("user") || "";
  const jobId = params.get("job") || "";
  const [explain, setExplain] = useState(false);
  const [result, setResult] = useState(null);

  const users = useApi(listUsers);
  const jobs = useApi(listJobs);
  const analysis = useAction(analyzeGap);
  const demo = useAction(loadDemo);

  const select = (key, value) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next, { replace: true });
  };

  const run = async (u = userId, j = jobId) => {
    const data = await analysis.run(u, j, explain);
    if (data) setResult(data);
  };

  const onDemo = async () => {
    const ok = await demo.run();
    if (ok) {
      await Promise.all([users.reload(), jobs.reload()]);
      setParams({ user: DEMO_USER.id, job: DEMO_JOB.id }, { replace: true });
      await run(DEMO_USER.id, DEMO_JOB.id);
    }
  };

  return (
    <div className="space-y-6">
      <Card
        title="Skill-gap analysis"
        subtitle="Pick a user and a job. Scores come from Neo4j and fixed rules, not from the LLM."
        actions={
          <Button variant="secondary" onClick={onDemo} pending={demo.pending}
            title="Creates demo_user and demo_job through the structured endpoints (no Gemini calls)">
            Load demo data
          </Button>
        }
      >
        <div className="grid sm:grid-cols-[1fr_1fr_auto] gap-3 items-end">
          <Field label="User">
            <Select value={userId} onChange={e => select("user", e.target.value)}>
              <option value="">{users.loading ? "Loading…" : "Select a user"}</option>
              {(users.data || []).map(u => (
                <option key={u.id} value={u.id}>{u.name || u.id} · {u.skill_count} skills</option>
              ))}
            </Select>
          </Field>
          <Field label="Job">
            <Select value={jobId} onChange={e => select("job", e.target.value)}>
              <option value="">{jobs.loading ? "Loading…" : "Select a job"}</option>
              {(jobs.data || []).map(j => (
                <option key={j.id} value={j.id}>{j.title} {j.company ? `· ${j.company}` : ""}</option>
              ))}
            </Select>
          </Field>
          <Button onClick={() => run()} disabled={!userId || !jobId} pending={analysis.pending}>
            Run analysis
          </Button>
        </div>
        <label className="flex items-center gap-2 mt-3 text-sm text-gray-600 dark:text-gray-300">
          <input type="checkbox" checked={explain} onChange={e => setExplain(e.target.checked)} className="accent-violet-600" />
          Add an LLM-written explanation <span className="text-xs text-gray-400">(uses 1 Gemini request)</span>
        </label>
        <div className="mt-3 space-y-2">
          <ErrorNote error={users.error || jobs.error} />
          <ErrorNote error={demo.error} onDismiss={demo.clearError} />
          <ErrorNote error={analysis.error} onDismiss={analysis.clearError} />
        </div>
        {!users.loading && !users.data?.length && (
          <p className="text-xs text-gray-500 mt-3">
            No users yet. Click <strong>Load demo data</strong>, or create them on the{" "}
            <Link to="/users" className="underline">Users</Link> and{" "}
            <Link to="/jobs" className="underline">Jobs</Link> pages.
          </p>
        )}
      </Card>

      {result && <AnalysisResult result={result} />}
      {userId && <PivotPanel userId={userId} jobId={jobId} />}
    </div>
  );
}

function AnalysisResult({ result }) {
  const [tone, label] = EXPLANATION_STATUS[result.explanation_status];
  const gaps = result.skill_breakdown.filter(s => s.status !== "met").length;

  return (
    <div className="space-y-6">
      <div className="grid md:grid-cols-[auto_1fr] gap-6">
        <Card className="flex items-center justify-center">
          <FitScore score={result.fit_score} domainBonus={result.domain_bonus} />
        </Card>
        <Card title={`${result.user_name || result.user_id} → ${result.job_title || result.job_id}`}
          subtitle={result.company || undefined}>
          <div className="flex flex-wrap gap-2 mb-4">
            <Badge tone="neutral">scoring v{result.scoring_version}</Badge>
            <Badge tone="neutral">gap score {result.gap_score}</Badge>
            <Badge tone={gaps ? "partial" : "met"}>{gaps} of {result.skill_breakdown.length} skills to close</Badge>
            <Badge tone={tone}>{label}</Badge>
          </div>
          {result.explanation
            ? <Explanation text={result.explanation} />
            : <p className="text-sm text-gray-500 dark:text-gray-400">
                The fit score is {Math.round(result.fit_score * 100)}%: required skills you fully meet,
                minus weighted gaps, with partial credit for prerequisites you already own.
                Tick "Add an LLM-written explanation" for a narrative.
              </p>}
        </Card>
      </div>

      <Card title="Skill graph"
        subtitle="The real Neo4j edges for this pair: what the job requires, what you have, and the prerequisite paths between them. Drag to explore; hover for details.">
        <AnalysisGraph graph={result.graph} />
      </Card>

      <Card title="Learning roadmap"
        subtitle="Steps in priority order (core first, biggest gap first). Teal cards are skills you already have that are prerequisites for a step.">
        <RoadmapFlow flow={result.roadmap_flow} />
      </Card>

      <div className="grid lg:grid-cols-2 gap-6">
        <Card title="Skill breakdown"><SkillBreakdown skills={result.skill_breakdown} /></Card>
        <Card title="Roadmap steps"><Roadmap roadmap={result.roadmap} /></Card>
      </div>

      <JsonToggle data={result} label="Raw /analyze response" />
    </div>
  );
}

function PivotPanel({ userId, jobId }) {
  const [role, setRole] = useState("");
  const [pivot, setPivot] = useState(null);
  const action = useAction(analyzePivot);

  const run = async () => {
    const data = await action.run(userId, role, jobId || null);
    if (data) setPivot(data);
  };

  return (
    <Card title="Career pivot (uses Gemini)"
      subtitle="Gemini describes the target role; the score itself uses fixed rules. Results are cached, so the same role is free the second time.">
      <div className="flex gap-3 items-end">
        <div className="flex-1">
          <Field label="Target role">
            <Input value={role} onChange={e => setRole(e.target.value)} placeholder="e.g. MLOps Engineer" />
          </Field>
        </div>
        <Button onClick={run} disabled={!role.trim()} pending={action.pending}>Analyze pivot</Button>
      </div>
      <div className="mt-3"><ErrorNote error={action.error} onDismiss={action.clearError} /></div>
      {pivot && (
        <div className="mt-4 space-y-3">
          <div className="flex gap-2 flex-wrap">
            <Badge tone="neutral">role profile from {pivot.extraction_source}</Badge>
            {pivot.unmapped_role_skills?.length > 0 && (
              <Badge tone="warning">{pivot.unmapped_role_skills.length} role skills not in ontology</Badge>
            )}
          </div>
          <PivotAnalysis pivot={pivot} />
        </div>
      )}
    </Card>
  );
}
