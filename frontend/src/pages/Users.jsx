import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  createUser, deleteUser, getUser, listUsers, replaceSkills, uploadResume,
} from "../api/client";
import { useAction, useApi } from "../hooks/useApi";
import { useOntologyNames } from "../hooks/useLayout";
import SkillRowsEditor from "../components/SkillRowsEditor";
import {
  Badge, Button, Card, Empty, ErrorNote, Field, Input, JsonToggle, Loading, Select, TextArea,
} from "../components/ui";

export default function Users() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("id");
  const users = useApi(listUsers);
  const select = id => setParams(id ? { id } : {}, { replace: true });

  return (
    <div className="grid lg:grid-cols-[320px_1fr] gap-6 items-start">
      <div className="space-y-6">
        <CreateUser onCreated={async id => { await users.reload(); select(id); }} />
        <Card title="Users" subtitle={users.data ? `${users.data.length} in the graph` : undefined}>
          <ErrorNote error={users.error} />
          {users.loading && !users.data ? <Loading /> : !users.data?.length ? <Empty>No users yet.</Empty> : (
            <ul className="divide-y divide-gray-100 dark:divide-gray-800 -mx-2">
              {users.data.map(u => (
                <li key={u.id}>
                  <button onClick={() => select(u.id)}
                    className={`w-full text-left px-2 py-2 rounded-lg ${selected === u.id ? "bg-violet-50 dark:bg-violet-950/40" : "hover:bg-gray-50 dark:hover:bg-gray-800/50"}`}>
                    <div className="text-sm font-medium text-gray-900 dark:text-white">{u.name || "(no name)"}</div>
                    <div className="text-xs text-gray-500 dark:text-gray-400 flex gap-2">
                      <span className="font-mono">{u.id}</span>·<span>{u.skill_count} skills</span>
                      {u.unmapped_count > 0 && <span className="text-amber-600">· {u.unmapped_count} unmapped</span>}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      {selected
        ? <UserDetail key={selected} id={selected} onDeleted={async () => { select(null); await users.reload(); }}
            onChanged={users.reload} />
        : <Card><Empty>Select a user, or create one.</Empty></Card>}
    </div>
  );
}

function CreateUser({ onCreated }) {
  const [form, setForm] = useState({ id: "", name: "", target_domain: "" });
  const { domains } = useOntologyNames();
  const action = useAction(createUser);

  const submit = async e => {
    e.preventDefault();
    const user = await action.run({
      id: form.id.trim() || null, name: form.name.trim(), target_domain: form.target_domain || null,
    });
    if (user) { setForm({ id: "", name: "", target_domain: "" }); onCreated(user.id); }
  };

  return (
    <Card title="New user" subtitle="No LLM involved. Add skills by hand or from a resume next.">
      <form onSubmit={submit} className="space-y-3">
        <Field label="Name"><Input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></Field>
        <Field label="Id" hint="Optional; generated if empty.">
          <Input value={form.id} onChange={e => setForm({ ...form, id: e.target.value })} placeholder="e.g. u_alex" />
        </Field>
        <Field label="Target domain">
          <Select value={form.target_domain} onChange={e => setForm({ ...form, target_domain: e.target.value })}>
            <option value="">None</option>
            {domains.map(d => <option key={d} value={d}>{d}</option>)}
          </Select>
        </Field>
        <ErrorNote error={action.error} onDismiss={action.clearError} />
        <Button type="submit" pending={action.pending} className="w-full">Create user</Button>
      </form>
    </Card>
  );
}

function UserDetail({ id, onDeleted, onChanged }) {
  const user = useApi(() => getUser(id), [id]);
  const remove = useAction(deleteUser);
  const [editing, setEditing] = useState(false);

  if (user.loading && !user.data) return <Card><Loading /></Card>;
  if (user.error) return <Card><ErrorNote error={user.error} /></Card>;
  const u = user.data;

  const onDelete = async () => {
    if (!window.confirm(`Delete user ${u.id}? This removes the user and all their skill edges.`)) return;
    const ok = await remove.run(u.id);
    if (ok !== undefined) onDeleted();
  };
  const refresh = async () => { await user.reload(); onChanged(); };

  return (
    <div className="space-y-6">
      <Card
        title={u.name || u.id}
        subtitle={<span className="font-mono">{u.id}</span>}
        actions={<>
          <Link to={`/?user=${encodeURIComponent(u.id)}`}><Button variant="secondary">Analyze →</Button></Link>
          <Button variant="danger" onClick={onDelete} pending={remove.pending}>Delete</Button>
        </>}
      >
        <ErrorNote error={remove.error} />
        <div className="flex gap-2 flex-wrap mb-4">
          <Badge tone="neutral">target domain: {u.target_domain || "none"}</Badge>
          <Badge tone="neutral">{u.skills.length} canonical skills</Badge>
        </div>

        {editing ? (
          <EditSkills user={u} onDone={async () => { setEditing(false); await refresh(); }} onCancel={() => setEditing(false)} />
        ) : (
          <>
            {u.skills.length === 0 ? <Empty>No skills yet.</Empty> : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-gray-500 dark:text-gray-400">
                    <th className="pb-2 font-medium">Skill</th><th className="pb-2 font-medium">Category</th>
                    <th className="pb-2 font-medium">Proficiency</th><th className="pb-2 font-medium">Source</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {u.skills.map(s => (
                    <tr key={s.name}>
                      <td className="py-2 font-medium text-gray-900 dark:text-white">{s.name}</td>
                      <td className="py-2 text-gray-500 dark:text-gray-400">{s.category}</td>
                      <td className="py-2">
                        <div className="flex items-center gap-2">
                          <div className="w-20 h-1.5 rounded-full bg-gray-200 dark:bg-gray-700">
                            <div className="h-1.5 rounded-full bg-violet-500" style={{ width: `${(s.proficiency || 0) * 100}%` }} />
                          </div>
                          <span className="text-xs tabular-nums text-gray-600 dark:text-gray-300">{Math.round((s.proficiency || 0) * 100)}%</span>
                        </div>
                      </td>
                      <td className="py-2"><Badge tone={s.source}>{s.source}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <Button variant="secondary" className="mt-4" onClick={() => setEditing(true)}>Edit skills by hand</Button>
          </>
        )}

        {u.unmapped_skills.length > 0 && (
          <div className="mt-5">
            <p className="text-xs font-medium text-gray-600 dark:text-gray-300 mb-1.5">
              Unmapped terms: not in the ontology, so stored as a list rather than as graph nodes
            </p>
            <div className="flex flex-wrap gap-1.5">
              {u.unmapped_skills.map(t => <Badge key={t} tone="warning">{t}</Badge>)}
            </div>
          </div>
        )}
      </Card>

      <ResumeUpload userId={u.id} onDone={refresh} />
      <JsonToggle data={u} label="Raw /users response" />
    </div>
  );
}

function EditSkills({ user, onDone, onCancel }) {
  const { skills: suggestions } = useOntologyNames();
  const [rows, setRows] = useState(user.skills.map(s => ({ name: s.name, proficiency: s.proficiency ?? 0.5 })));
  const action = useAction(replaceSkills);

  const save = async () => {
    const clean = rows.filter(r => r.name.trim()).map(r => ({ name: r.name.trim(), proficiency: r.proficiency }));
    const updated = await action.run(user.id, clean);
    if (updated) onDone();
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-amber-700 dark:text-amber-300">
        Saving replaces the user's entire skill list, including skills that came from a resume.
      </p>
      <SkillRowsEditor rows={rows} onChange={setRows} valueKey="proficiency" valueLabel="Proficiency" suggestions={suggestions} />
      <ErrorNote error={action.error} onDismiss={action.clearError} />
      <div className="flex gap-2">
        <Button onClick={save} pending={action.pending}>Save skills</Button>
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

function ResumeUpload({ userId, onDone }) {
  const [text, setText] = useState("");
  const [result, setResult] = useState(null);
  const action = useAction(uploadResume);

  const submit = async () => {
    const r = await action.run(userId, text);
    if (r) { setResult(r); onDone(); }
  };

  return (
    <Card title="Update from resume text (uses Gemini)"
      subtitle="Replaces resume-derived skills and keeps hand-entered ones. Identical text is detected and skipped for free.">
      <TextArea rows={6} value={text} onChange={e => setText(e.target.value)} placeholder="Paste resume text…" />
      <div className="flex items-center gap-3 mt-3">
        <Button onClick={submit} disabled={!text.trim()} pending={action.pending}>Parse resume</Button>
        {result && (
          <div className="flex gap-2 flex-wrap">
            <Badge tone="neutral">{result.status}</Badge>
            <Badge tone="neutral">source: {result.extraction_source}</Badge>
            {result.skills_written && <Badge tone="met">{result.skills_written.length} skills written</Badge>}
            {result.unmapped_skills?.length > 0 && <Badge tone="warning">{result.unmapped_skills.length} unmapped</Badge>}
          </div>
        )}
      </div>
      <div className="mt-3"><ErrorNote error={action.error} onDismiss={action.clearError} /></div>
    </Card>
  );
}
