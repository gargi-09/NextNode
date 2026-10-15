import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { createJob, createJobFromText, deleteJob, getJob, listJobs } from "../api/client";
import { useAction, useApi } from "../hooks/useApi";
import { useOntologyNames } from "../hooks/useLayout";
import SkillRowsEditor from "../components/SkillRowsEditor";
import {
  Badge, Button, Card, Empty, ErrorNote, Field, Input, JsonToggle, Loading, Select, TextArea,
} from "../components/ui";

const TIERS = [
  ["core_skills", "Core (required)"],
  ["preferred_skills", "Preferred"],
  ["nice_to_have_skills", "Nice to have"],
];

export default function Jobs() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("id");
  const jobs = useApi(listJobs);
  const select = id => setParams(id ? { id } : {}, { replace: true });

  return (
    <div className="space-y-6">
      <CreateJob onCreated={async id => { await jobs.reload(); select(id); }} />
      <div className="grid lg:grid-cols-[320px_1fr] gap-6 items-start">
        <Card title="Jobs" subtitle={jobs.data ? `${jobs.data.length} in the graph` : undefined}>
          <ErrorNote error={jobs.error} />
          {jobs.loading && !jobs.data ? <Loading /> : !jobs.data?.length ? <Empty>No jobs yet.</Empty> : (
            <ul className="divide-y divide-gray-100 dark:divide-gray-800 -mx-2">
              {jobs.data.map(j => (
                <li key={j.id}>
                  <button onClick={() => select(j.id)}
                    className={`w-full text-left px-2 py-2 rounded-lg ${selected === j.id ? "bg-violet-50 dark:bg-violet-950/40" : "hover:bg-gray-50 dark:hover:bg-gray-800/50"}`}>
                    <div className="text-sm font-medium text-gray-900 dark:text-white">{j.title}</div>
                    <div className="text-xs text-gray-500 dark:text-gray-400 flex gap-2 flex-wrap">
                      {j.company && <span>{j.company} ·</span>}
                      <span className="font-mono">{j.id.slice(0, 18)}</span>·<span>{j.skill_count} skills</span>
                      {j.unmapped_count > 0 && <span className="text-amber-600">· {j.unmapped_count} unmapped</span>}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        {selected
          ? <JobDetail key={selected} id={selected} onDeleted={async () => { select(null); await jobs.reload(); }} />
          : <Card><Empty>Select a job, or create one above.</Empty></Card>}
      </div>
    </div>
  );
}

function CreateJob({ onCreated }) {
  const [mode, setMode] = useState("structured");
  return (
    <Card title="New job"
      actions={
        <div className="inline-flex rounded-lg border border-gray-200 dark:border-gray-700 p-0.5">
          {[["structured", "Structured (no LLM)"], ["text", "From description (Gemini)"]].map(([m, label]) => (
            <button key={m} onClick={() => setMode(m)}
              className={`px-3 py-1 text-xs rounded-md ${mode === m ? "bg-violet-600 text-white" : "text-gray-600 dark:text-gray-300"}`}>
              {label}
            </button>
          ))}
        </div>
      }>
      {mode === "structured" ? <StructuredJobForm onCreated={onCreated} /> : <TextJobForm onCreated={onCreated} />}
    </Card>
  );
}

const EMPTY_JOB = {
  id: "", title: "", company: "", seniority: "mid", domains: [],
  core_skills: [{ name: "", weight: 0.6 }], preferred_skills: [], nice_to_have_skills: [],
};

function StructuredJobForm({ onCreated }) {
  const { skills: suggestions, domains } = useOntologyNames();
  const [job, setJob] = useState(EMPTY_JOB);
  const action = useAction(createJob);
  const set = patch => setJob(j => ({ ...j, ...patch }));

  const submit = async e => {
    e.preventDefault();
    const clean = rows => rows.filter(r => r.name.trim()).map(r => ({ name: r.name.trim(), weight: r.weight }));
    const created = await action.run({
      id: job.id.trim() || null, title: job.title.trim(), company: job.company.trim() || null,
      seniority: job.seniority || null, domains: job.domains,
      core_skills: clean(job.core_skills), preferred_skills: clean(job.preferred_skills),
      nice_to_have_skills: clean(job.nice_to_have_skills),
    });
    if (created) { setJob(EMPTY_JOB); onCreated(created.id); }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="grid sm:grid-cols-4 gap-3">
        <Field label="Title"><Input required value={job.title} onChange={e => set({ title: e.target.value })} /></Field>
        <Field label="Company"><Input value={job.company} onChange={e => set({ company: e.target.value })} /></Field>
        <Field label="Seniority">
          <Select value={job.seniority} onChange={e => set({ seniority: e.target.value })}>
            {["junior", "mid", "senior", "staff"].map(s => <option key={s}>{s}</option>)}
          </Select>
        </Field>
        <Field label="Id" hint="Optional"><Input value={job.id} onChange={e => set({ id: e.target.value })} /></Field>
      </div>
      <div>
        <span className="block text-xs font-medium text-gray-600 dark:text-gray-300 mb-1">Domains</span>
        <div className="flex flex-wrap gap-3">
          {domains.map(d => (
            <label key={d} className="flex items-center gap-1.5 text-sm text-gray-700 dark:text-gray-300">
              <input type="checkbox" className="accent-violet-600" checked={job.domains.includes(d)}
                onChange={e => set({ domains: e.target.checked ? [...job.domains, d] : job.domains.filter(x => x !== d) })} />
              {d}
            </label>
          ))}
        </div>
      </div>
      <div className="grid lg:grid-cols-3 gap-4">
        {TIERS.map(([key, label]) => (
          <div key={key} className="rounded-xl border border-gray-100 dark:border-gray-800 p-3">
            <p className="text-xs font-semibold text-gray-700 dark:text-gray-200 mb-2">{label}</p>
            <SkillRowsEditor rows={job[key]} onChange={rows => set({ [key]: rows })}
              valueKey="weight" valueLabel="Required level" suggestions={suggestions} />
          </div>
        ))}
      </div>
      <ErrorNote error={action.error} onDismiss={action.clearError} />
      <Button type="submit" pending={action.pending}>Create job</Button>
    </form>
  );
}

function TextJobForm({ onCreated }) {
  const [text, setText] = useState("");
  const [id, setId] = useState("");
  const [result, setResult] = useState(null);
  const action = useAction(createJobFromText);

  const submit = async () => {
    const r = await action.run(text, id.trim() || null);
    if (r) { setResult(r); onCreated(r.job_id); }
  };

  return (
    <div className="space-y-3">
      <TextArea rows={7} value={text} onChange={e => setText(e.target.value)} placeholder="Paste the job description…" />
      <div className="flex gap-3 items-end">
        <div className="w-60"><Field label="Id (optional)"><Input value={id} onChange={e => setId(e.target.value)} /></Field></div>
        <Button onClick={submit} disabled={!text.trim()} pending={action.pending}>Parse and create</Button>
      </div>
      <ErrorNote error={action.error} onDismiss={action.clearError} />
      {result && (
        <div className="flex gap-2 flex-wrap">
          <Badge tone="neutral">{result.status}</Badge>
          <Badge tone="neutral">source: {result.extraction_source}</Badge>
          {result.skills_written && <Badge tone="met">{result.skills_written.length} canonical skills</Badge>}
          {result.unmapped_skills?.length > 0 && <Badge tone="warning">unmapped: {result.unmapped_skills.join(", ")}</Badge>}
          {result.unmapped_domains?.length > 0 && <Badge tone="warning">unknown domains: {result.unmapped_domains.join(", ")}</Badge>}
        </div>
      )}
    </div>
  );
}

function JobDetail({ id, onDeleted }) {
  const job = useApi(() => getJob(id), [id]);
  const remove = useAction(deleteJob);

  if (job.loading && !job.data) return <Card><Loading /></Card>;
  if (job.error) return <Card><ErrorNote error={job.error} /></Card>;
  const j = job.data;

  const onDelete = async () => {
    if (!window.confirm(`Delete job ${j.id}? This removes the posting and its requirement edges.`)) return;
    const ok = await remove.run(j.id);
    if (ok !== undefined) onDeleted();
  };

  return (
    <div className="space-y-6">
      <Card title={j.title} subtitle={[j.company, j.seniority].filter(Boolean).join(" · ") || undefined}
        actions={<>
          <Link to={`/?job=${encodeURIComponent(j.id)}`}><Button variant="secondary">Analyze →</Button></Link>
          <Button variant="danger" onClick={onDelete} pending={remove.pending}>Delete</Button>
        </>}>
        <ErrorNote error={remove.error} />
        <div className="flex gap-2 flex-wrap mb-4">
          <Badge tone="neutral"><span className="font-mono">{j.id}</span></Badge>
          {j.domains.map(d => <Badge key={d} tone="preferred">{d}</Badge>)}
        </div>
        {j.skills.length === 0 ? <Empty>No canonical skills.</Empty> : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 dark:text-gray-400">
                <th className="pb-2 font-medium">Skill</th><th className="pb-2 font-medium">Tier</th>
                <th className="pb-2 font-medium">Required level</th><th className="pb-2 font-medium">Category</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {j.skills.map(s => (
                <tr key={s.name}>
                  <td className="py-2 font-medium text-gray-900 dark:text-white">{s.name}</td>
                  <td className="py-2"><Badge tone={s.importance}>{s.importance.replace(/_/g, " ")}</Badge></td>
                  <td className="py-2 tabular-nums text-gray-600 dark:text-gray-300">{Math.round((s.weight || 0) * 100)}%</td>
                  <td className="py-2 text-gray-500 dark:text-gray-400">{s.category}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {j.unmapped_skills.length > 0 && (
          <div className="mt-5">
            <p className="text-xs font-medium text-gray-600 dark:text-gray-300 mb-1.5">Unmapped terms (not graph nodes)</p>
            <div className="flex flex-wrap gap-1.5">{j.unmapped_skills.map(t => <Badge key={t} tone="warning">{t}</Badge>)}</div>
          </div>
        )}
      </Card>
      <JsonToggle data={j} label="Raw /jobs response" />
    </div>
  );
}
