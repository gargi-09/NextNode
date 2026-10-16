import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import registry from "../features/features.json";
import { buildRequest, initialValues, send } from "../features/request";
import { Badge, Button, Card, Input, TextArea } from "../components/ui";

const STATUS = {
  live:     { tone: "met",     label: "live" },
  built:    { tone: "preferred", label: "built, not running" },
  designed: { tone: "warning", label: "designed" },
  planned:  { tone: "neutral", label: "planned" },
};

export default function Features() {
  const [category, setCategory] = useState("All");
  const [query, setQuery] = useState("");
  const { features, categories, changelog, updated } = registry;

  const counts = useMemo(() => {
    const by = s => features.filter(f => f.status === s).length;
    return {
      live: by("live"), built: by("built"), designed: by("designed"), planned: by("planned"),
      endpoints: new Set(features.flatMap(f => f.endpoints)).size,
      runnable: features.filter(f => f.tryIt).length,
    };
  }, [features]);

  const visible = features.filter(f =>
    (category === "All" || f.category === category) &&
    (!query || `${f.title} ${f.summary}`.toLowerCase().includes(query.toLowerCase())));

  return (
    <div className="space-y-6">
      <Card title="Features dashboard"
        subtitle={`Everything implemented so far, how it works, and a live tester for each. Updated ${updated}.`}>
        <div className="flex flex-wrap gap-2 mb-4">
          <Badge tone="met">{counts.live} live</Badge>
          <Badge tone="preferred">{counts.built} built, not running</Badge>
          <Badge tone="warning">{counts.designed} designed</Badge>
          <Badge tone="neutral">{counts.planned} planned</Badge>
          <Badge tone="neutral">{counts.endpoints} API endpoints covered</Badge>
          <Badge tone="neutral">{counts.runnable} runnable testers</Badge>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {["All", ...categories].map(c => (
            <button key={c} onClick={() => setCategory(c)}
              className={`px-3 py-1 rounded-full text-xs font-medium border ${category === c
                ? "bg-violet-600 border-violet-600 text-white"
                : "border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"}`}>
              {c}
            </button>
          ))}
          <div className="ml-auto w-56"><Input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search features…" /></div>
        </div>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-3">
          Testers call your running API at the same address as the rest of the app. Badges show which
          ones <strong>write to Neo4j</strong> or <strong>use a Gemini request</strong>; those ask before running.
          Defaults use <code>dash_user</code> / <code>dash_job</code>, so run them roughly in order:
          create user → set skills → create job → analyze → delete.
        </p>
      </Card>

      {visible.map(f => <FeatureCard key={f.id} feature={f} />)}

      <Card title="Changelog" subtitle="Completed tasks and the features they added">
        <ol className="space-y-3">
          {[...changelog].reverse().map((entry, i) => (
            <li key={i} className="flex gap-3">
              <span className="text-xs tabular-nums text-gray-400 w-20 flex-shrink-0 pt-0.5">{entry.date}</span>
              <div>
                <p className="text-sm text-gray-800 dark:text-gray-200">{entry.task}</p>
                <div className="flex flex-wrap gap-1 mt-1">
                  {entry.features.map(id => <Badge key={id} tone="neutral">{features.find(f => f.id === id)?.title || id}</Badge>)}
                </div>
              </div>
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}

function FeatureCard({ feature: f }) {
  const status = STATUS[f.status] || STATUS.planned;
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <h3 className="text-base font-semibold text-gray-900 dark:text-white">{f.title}</h3>
            <Badge tone={status.tone}>{status.label}</Badge>
            <Badge tone="neutral">{f.category}</Badge>
            {f.llm && <Badge tone="resume">uses Gemini</Badge>}
            {f.writes && <Badge tone={f.destructive ? "missing" : "partial"}>{f.destructive ? "deletes data" : "writes to Neo4j"}</Badge>}
          </div>
          <p className="text-sm text-gray-600 dark:text-gray-300">{f.summary}</p>
        </div>
        {f.openPage && <Link to={f.openPage}><Button variant="secondary">Open page →</Button></Link>}
      </div>

      <details className="mt-3 group">
        <summary className="text-xs font-medium text-violet-600 dark:text-violet-400 cursor-pointer select-none">How it works</summary>
        <ol className="list-decimal pl-5 mt-2 space-y-1 text-sm text-gray-600 dark:text-gray-300">
          {f.how.map((step, i) => <li key={i}>{step}</li>)}
        </ol>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {f.files.map(file => <code key={file} className="text-[11px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300">{file}</code>)}
          {f.endpoints.map(e => <code key={e} className="text-[11px] px-1.5 py-0.5 rounded bg-violet-50 dark:bg-violet-950/50 text-violet-700 dark:text-violet-300">{e}</code>)}
        </div>
      </details>

      {f.tryIt
        ? <Tester feature={f} />
        : <p className="mt-3 text-xs text-gray-400 dark:text-gray-500">
            {f.status === "live" ? "Not an HTTP feature: see How it works for how to run it." : "Not runnable yet."}
          </p>}
    </Card>
  );
}

function Tester({ feature: f }) {
  const [values, setValues] = useState(() => initialValues(f.tryIt));
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);

  const run = async () => {
    setError(null);
    let request;
    try {
      request = buildRequest(f.tryIt, values);
    } catch (e) {
      setError(e.message);
      return;
    }
    const usesGemini = f.llm || values.explain === true; // analyze only uses Gemini when explain is ticked
    const warnings = [
      f.destructive && "This DELETES data in your Neo4j graph.",
      f.writes && !f.destructive && "This writes to your Neo4j graph.",
      usesGemini && "This uses Gemini requests from your daily free-tier budget.",
    ].filter(Boolean);
    if (warnings.length && !window.confirm(`${warnings.join("\n")}\n\nRun ${f.tryIt.method} ${f.tryIt.path}?`)) return;

    setPending(true);
    setResult(await send(request));
    setPending(false);
  };

  const ok = result && result.status >= 200 && result.status < 300;

  return (
    <div className="mt-4 rounded-xl border border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-950/60 p-3 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-bold px-1.5 py-0.5 rounded bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900">{f.tryIt.method}</span>
        <code className="text-xs text-gray-600 dark:text-gray-300">{f.tryIt.path}</code>
      </div>
      {f.tryIt.inputs.length > 0 && (
        <div className="grid sm:grid-cols-2 gap-3">
          {f.tryIt.inputs.map(input => (
            <TesterInput key={input.name} input={input} value={values[input.name]}
              onChange={v => setValues(s => ({ ...s, [input.name]: v }))} />
          ))}
        </div>
      )}
      <div className="flex items-center gap-3">
        <Button onClick={run} pending={pending}>Run</Button>
        {result && (
          <span className={`text-xs font-medium ${ok ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
            {result.status || "network error"} · {result.ms} ms
            {result.retryAfter ? ` · Retry-After ${result.retryAfter}s` : ""}
          </span>
        )}
        {error && <span className="text-xs text-red-600">{error}</span>}
      </div>
      {result && (
        <pre className="max-h-80 overflow-auto rounded-lg bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 p-3 text-xs text-gray-700 dark:text-gray-300">
          {result.data === "" ? "(empty body)" : JSON.stringify(result.data, null, 2)}
        </pre>
      )}
    </div>
  );
}

function TesterInput({ input, value, onChange }) {
  const wide = input.type === "textarea" || input.type === "json";
  return (
    <label className={`block ${wide ? "sm:col-span-2" : ""}`}>
      <span className="block text-xs font-medium text-gray-600 dark:text-gray-300 mb-1">
        {input.label} <span className="text-gray-400 font-normal">({input.in})</span>
      </span>
      {input.type === "checkbox" ? (
        <input type="checkbox" checked={Boolean(value)} onChange={e => onChange(e.target.checked)} className="accent-violet-600" />
      ) : wide ? (
        <TextArea rows={input.type === "json" ? 3 : 3} value={value} onChange={e => onChange(e.target.value)}
          className={input.type === "json" ? "font-mono text-xs" : ""} />
      ) : (
        <Input type={input.type === "number" ? "number" : "text"} value={value} onChange={e => onChange(e.target.value)} />
      )}
    </label>
  );
}
