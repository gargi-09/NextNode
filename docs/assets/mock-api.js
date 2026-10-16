/* In-browser stand-in for the NextNode API, used by sandbox.html when no backend is running.
 * Same routes, status codes and response shapes as the FastAPI app; graph logic comes from
 * nextnode-core.js (a port of the Python engine). Differences, by design:
 *   - data lives in this browser's localStorage, not Neo4j;
 *   - resume / job text is parsed by a deterministic keyword matcher instead of Gemini;
 *   - pivot uses built-in role templates; explanations are templated.
 */
(function () {
  "use strict";
  const NN = window.NN, D = window.NEXTNODE;
  const KEY = "nextnode-sandbox-v1";
  const EXTRACTOR = "sandbox-keyword-parser";

  let store = load();
  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || { users: {}, jobs: {} }; }
    catch { return { users: {}, jobs: {} }; }
  }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(store)); } catch { /* storage unavailable: in-memory only */ } }

  /* ── helpers ── */
  const res = (status, data, headers = {}) => ({ status, data, headers });
  const notFound = msg => res(404, { detail: msg });
  const conflict = msg => res(409, { detail: msg });
  const invalid = (loc, msg) => res(422, { detail: [{ type: "value_error", loc, msg }] });
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : `id-${Date.now()}-${Math.random().toString(16).slice(2)}`);
  const hash = text => { // stable content hash of whitespace-normalized text (stands in for sha256)
    const s = String(text).normalize("NFKC").replace(/\s+/g, " ").trim();
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return (h >>> 0).toString(16);
  };
  const MAX_UNMAPPED = 50;

  function ownedRows(skills) { // app/graph/normalize.owned_skill_rows
    const rows = {}, unmapped = [];
    for (const s of skills) {
      const name = String(s.name || "").trim(); if (!name) continue;
      const r = NN.resolve(name);
      if (!r) { if (!unmapped.includes(name) && unmapped.length < MAX_UNMAPPED) unmapped.push(name); continue; }
      const cur = rows[r.skill.name];
      if (!cur || s.proficiency > cur.proficiency) rows[r.skill.name] = { name: r.skill.name, proficiency: s.proficiency, level: s.level ?? null };
    }
    return { rows: Object.values(rows), unmapped };
  }
  function requiredRows(tiers) { // app/graph/normalize.required_skill_rows (tier precedence)
    const rows = {}, unmapped = [];
    for (const importance of ["core", "preferred", "nice_to_have"]) for (const s of tiers[importance] || []) {
      const name = String(s.name || "").trim(); if (!name) continue;
      const r = NN.resolve(name);
      if (!r) { if (!unmapped.includes(name) && unmapped.length < MAX_UNMAPPED) unmapped.push(name); continue; }
      const cur = rows[r.skill.name];
      if (!cur) rows[r.skill.name] = { name: r.skill.name, importance, weight: s.weight };
      else if (cur.importance === importance && s.weight > cur.weight) cur.weight = s.weight;
    }
    return { rows: Object.values(rows), unmapped };
  }
  function resolveDomains(list) {
    const keys = [], unknown = [];
    for (const d of list || []) {
      const k = NN.resolveDomain(d);
      if (k) { if (!keys.includes(k)) keys.push(k); }
      else if (d && String(d).trim().toLowerCase() !== "null") unknown.push(String(d).trim());
    }
    return { keys, unknown };
  }
  const checkRange = (list, field, path) => {
    for (let i = 0; i < (list || []).length; i++) {
      const v = list[i][field];
      if (typeof v !== "number" || v < 0 || v > 1) return invalid([...path, i, field], "Input should be between 0 and 1");
      if (!String(list[i].name || "").trim()) return invalid([...path, i, "name"], "String should have at least 1 character");
    }
    return null;
  };

  /* ── views (shapes of app/graph/catalog.py) ── */
  function userView(u) {
    const skills = Object.entries(u.skills).map(([name, e]) => ({ name, key: NN.BY_NAME[name].key,
      category: NN.BY_NAME[name].category, proficiency: e.proficiency, level: e.level, source: e.source }))
      .sort((a, b) => (b.proficiency || 0) - (a.proficiency || 0));
    return { id: u.id, name: u.name, target_domain: u.target_domain, unmapped_skills: u.unmapped_skills, skills };
  }
  function jobView(j) {
    const skills = Object.entries(j.skills).map(([name, e]) => ({ name, key: NN.BY_NAME[name].key,
      category: NN.BY_NAME[name].category, importance: e.importance, weight: e.weight, level: null }))
      .sort((a, b) => (NN.TIER[a.importance] - NN.TIER[b.importance]) || (b.weight - a.weight));
    return { id: j.id, title: j.title, company: j.company, seniority: j.seniority, skills, domains: j.domains,
             unmapped_skills: j.unmapped_skills };
  }

  /* ── text "parsing" (Gemini's job in the real system) ── */
  function parseResume(text) {
    const found = NN.lexicalSkills(text);
    const name = String(text).split(/[.,\n]/)[0].trim().slice(0, 60) || "Unnamed";
    const counts = {};
    for (const f of found) for (const d of NN.BY_NAME[f.name].domains) counts[d] = (counts[d] || 0) + 1;
    const target = Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
    return { name, target_domain: target, skills: found.map(f => ({ name: f.as, proficiency: 0.6 })) };
  }
  function parseJob(text) {
    const lower = String(text).toLowerCase();
    const seniority = ["staff", "senior", "junior", "mid"].find(s => lower.includes(s)) || "mid";
    const weight = { junior: 0.4, mid: 0.6, senior: 0.8, staff: 0.9 }[seniority];
    const tiers = { core: [], preferred: [], nice_to_have: [] };
    // split on tier markers; text before any marker counts as core
    const parts = String(text).split(/(required|must have|requirements|preferred|nice to have|bonus|plus)\s*:/i);
    let tier = "core";
    for (const part of parts) {
      const m = part.toLowerCase();
      if (["required", "must have", "requirements"].includes(m)) { tier = "core"; continue; }
      if (["preferred", "plus"].includes(m)) { tier = "preferred"; continue; }
      if (["nice to have", "bonus"].includes(m)) { tier = "nice_to_have"; continue; }
      for (const f of NN.lexicalSkills(part)) tiers[tier].push({ name: f.as, weight: tier === "core" ? weight : Math.round(weight * 0.8 * 100) / 100 });
    }
    const title = String(text).split(/[.\n]/)[0].replace(/\s+at\s+.*/i, "").trim().slice(0, 80) || "Untitled job";
    const company = (String(text).match(/\bat\s+([A-Z][\w&.-]*(?:\s[A-Z][\w&.-]*)?)/) || [])[1] || null;
    const domains = [...new Set(tiers.core.flatMap(s => NN.BY_NAME[NN.resolve(s.name).skill.name].domains))].slice(0, 2);
    return { title, company, seniority, core_skills: tiers.core, preferred_skills: tiers.preferred,
             nice_to_have_skills: tiers.nice_to_have, domains };
  }

  /* ── writes (app/graph/writer.py semantics) ── */
  function writeResume(userId, extracted, sourceHash) {
    const created = !store.users[userId];
    const u = store.users[userId] ||= { id: userId, name: null, target_domain: null, skills: {}, unmapped_skills: [] };
    const { rows, unmapped } = ownedRows(extracted.skills);
    u.name = u.name ?? extracted.name;
    const domain = NN.resolveDomain(extracted.target_domain);
    if (domain) u.target_domain = domain;
    for (const [n, e] of Object.entries(u.skills)) if (e.source === "resume") delete u.skills[n];
    for (const r of rows) if (!u.skills[r.name] || u.skills[r.name].source === "resume")
      u.skills[r.name] = { proficiency: r.proficiency, level: r.level, source: "resume" };
    u.unmapped_skills = unmapped; u.source_hash = sourceHash;
    save();
    return { created, rows, unmapped };
  }
  function writeJob(jobId, meta, tiers, domains, sourceHash) {
    const created = !store.jobs[jobId];
    const { rows, unmapped } = requiredRows(tiers);
    const d = resolveDomains(domains);
    store.jobs[jobId] = { id: jobId, title: meta.title, company: meta.company ?? null, seniority: meta.seniority ?? null,
      skills: Object.fromEntries(rows.map(r => [r.name, { importance: r.importance, weight: r.weight }])),
      domains: d.keys, unmapped_skills: unmapped, source_hash: sourceHash ?? null };
    save();
    return { created, rows, unmapped, unknownDomains: d.unknown };
  }

  /* ── analysis ── */
  function analyze(userId, jobId, explain) {
    const u = store.users[userId], j = store.jobs[jobId];
    if (!u) return notFound(`User '${userId}' not found`);
    if (!j) return notFound(`Job '${jobId}' not found`);
    const owned = Object.fromEntries(Object.entries(u.skills).map(([n, e]) => [n, e.proficiency]));
    const reqs = Object.entries(j.skills).map(([n, e]) => ({ skill: n, importance: e.importance, weight: e.weight }));
    const a = NN.analyzeProfile(owned, reqs, u.target_domain, j.domains);
    const userDomains = u.target_domain ? [u.target_domain] : [];
    const r = a.result;
    const explanation = explain
      ? `## Overall fit\nSandbox explanation (templated; Gemini writes this in the real system). Fit is ${NN.pct(r.fit_score)} with ` +
        `${r.roadmap.length} gap(s).\n\n## Gaps to close\n` + r.roadmap.map(s => `- **${s.skill}** (${s.priority}): ${s.reason}`).join("\n")
      : null;
    return res(200, {
      user_id: userId, job_id: jobId, user_name: u.name, job_title: j.title, company: j.company,
      scoring_version: D.scoringVersion, fit_score: r.fit_score, gap_score: r.gap_score, domain_bonus: r.domain_bonus,
      skill_breakdown: r.skill_breakdown, roadmap: r.roadmap,
      graph: NN.buildGraphPayload(a, u, j, userDomains, j.domains),
      roadmap_flow: NN.buildRoadmapFlow(a),
      explanation, explanation_status: explain ? "generated" : "not_requested",
    });
  }

  /* ── routes ── */
  const ROUTES = [
    ["GET", /^\/health$/, () => res(200, { status: "ok" })],
    ["GET", /^\/health\/graph$/, () => {
      const users = Object.values(store.users), jobs = Object.values(store.jobs);
      const rels = users.reduce((n, u) => n + Object.keys(u.skills).length + (u.target_domain ? 1 : 0), 0)
        + jobs.reduce((n, j) => n + Object.keys(j.skills).length + j.domains.length, 0);
      return res(200, { graph: "ok", breaker: "closed", sandbox: true,
        nodes: D.ontology.skills.length + D.ontology.domains.length + users.length + jobs.length,
        relationships: D.ontology.prerequisites.length + rels, canonical_skills: D.ontology.skills.length,
        gemini: { rpd_limit: 200, rpd_used: 0, rpm_limit: 8, rpm_used: 0, cooldown_s: 0 } });
    }],
    ["GET", /^\/api\/v1\/ontology\/resolve$/, (m, q) => {
      if (!q.names) return invalid(["query", "names"], "Field required");
      const resolved = [], unmapped = [];
      for (const name of String(q.names).split(/\n/).flatMap(c => c.split(",")).map(s => s.trim()).filter(Boolean)) {
        const r = NN.resolve(name);
        if (r) resolved.push({ input: name, canonical: r.skill.name, key: r.skill.key, category: r.skill.category,
                               via: NN.canonicalKey(name) === r.skill.key ? "name" : "alias" });
        else if (!unmapped.includes(name)) unmapped.push(name);
      }
      return res(200, { resolved, unmapped });
    }],
    ["GET", /^\/api\/v1\/ontology\/graph$/, () => {
      const sid = n => `skill:${n}`;
      return res(200, { version: D.ontology.version,
        nodes: [...D.ontology.skills.map(s => ({ id: sid(s.name), label: s.name, type: "skill", category: s.category, level: s.level, val: 2 })),
                ...D.ontology.domains.map(d => ({ id: `domain:${d}`, label: d, type: "domain", val: 4 }))],
        links: [...D.ontology.prerequisites.map(([a, b, w]) => ({ source: sid(a), target: sid(b), type: "PREREQUISITE_OF", strength: w })),
                ...D.ontology.skills.flatMap(s => s.domains.map(d => ({ source: sid(s.name), target: `domain:${d}`, type: "BELONGS_TO" })))] });
    }],
    ["GET", /^\/api\/v1\/ontology\/unmapped$/, () => {
      const counts = {};
      for (const [kind, all] of [["users", store.users], ["jobs", store.jobs]]) for (const n of Object.values(all)) for (const t of n.unmapped_skills) {
        const k = t.trim().toLowerCase(); counts[k] ||= { term: k, freq: 0, in_users: 0, in_jobs: 0 };
        counts[k].freq++; counts[k][kind === "users" ? "in_users" : "in_jobs"]++;
      }
      return res(200, Object.values(counts).sort((a, b) => b.freq - a.freq || a.term.localeCompare(b.term)));
    }],
    ["POST", /^\/api\/v1\/users$/, (m, q, b) => {
      if (!b?.name || !String(b.name).trim()) return invalid(["body", "name"], "Field required");
      const id = (b.id && String(b.id).trim()) || uuid();
      if (store.users[id]) return conflict(`User '${id}' already exists`);
      store.users[id] = { id, name: String(b.name).trim(), target_domain: NN.resolveDomain(b.target_domain), skills: {}, unmapped_skills: [] };
      save();
      return res(201, userView(store.users[id]));
    }],
    ["GET", /^\/api\/v1\/users$/, (m, q) => {
      const limit = q.limit == null ? 50 : Number(q.limit);
      if (!(limit >= 1 && limit <= 200)) return invalid(["query", "limit"], "Input should be between 1 and 200");
      return res(200, Object.values(store.users).sort((a, b) => (a.name || "").localeCompare(b.name || "") || a.id.localeCompare(b.id))
        .slice(Number(q.skip || 0), Number(q.skip || 0) + limit)
        .map(u => ({ id: u.id, name: u.name, target_domain: u.target_domain, skill_count: Object.keys(u.skills).length, unmapped_count: u.unmapped_skills.length })));
    }],
    ["GET", /^\/api\/v1\/users\/([^/]+)$/, m => store.users[m[1]] ? res(200, userView(store.users[m[1]])) : notFound(`User '${m[1]}' not found`)],
    ["PUT", /^\/api\/v1\/users\/([^/]+)\/skills$/, (m, q, b) => {
      if (!Array.isArray(b?.skills)) return invalid(["body", "skills"], "Field required");
      const bad = checkRange(b.skills, "proficiency", ["body", "skills"]); if (bad) return bad;
      const u = store.users[m[1]]; if (!u) return notFound(`User '${m[1]}' not found`);
      const { rows, unmapped } = ownedRows(b.skills);
      u.skills = Object.fromEntries(rows.map(r => [r.name, { proficiency: r.proficiency, level: r.level, source: "manual" }]));
      u.unmapped_skills = unmapped; save();
      return res(200, userView(u));
    }],
    ["POST", /^\/api\/v1\/users\/([^/]+)\/resume$|^\/api\/v1\/extract\/resume$/, (m, q, b) => {
      const userId = m[1] || b?.user_id;
      if (!b?.text || !String(b.text).trim()) return invalid(["body", "text"], "String should have at least 1 character");
      if (!userId) return invalid(["body", "user_id"], "Field required");
      const h = hash(b.text);
      if (store.users[userId]?.source_hash === h) return res(200, { user_id: userId, status: "unchanged",
        extraction_source: "skipped", skills_written: null, unmapped_skills: null, extracted: null });
      const extracted = parseResume(b.text);
      const w = writeResume(userId, extracted, h);
      return res(200, { user_id: userId, status: w.created ? "created" : "updated", extraction_source: EXTRACTOR,
        skills_written: w.rows.map(r => r.name), unmapped_skills: w.unmapped, extracted });
    }],
    ["DELETE", /^\/api\/v1\/users\/([^/]+)$/, m => {
      if (!store.users[m[1]]) return notFound(`User '${m[1]}' not found`);
      delete store.users[m[1]]; save(); return res(204, "");
    }],
    ["POST", /^\/api\/v1\/jobs$/, (m, q, b) => {
      if (!b?.title || !String(b.title).trim()) return invalid(["body", "title"], "Field required");
      for (const t of ["core_skills", "preferred_skills", "nice_to_have_skills"]) {
        const bad = checkRange(b[t], "weight", ["body", t]); if (bad) return bad;
      }
      const id = (b.id && String(b.id).trim()) || uuid();
      if (store.jobs[id]) return conflict(`Job '${id}' already exists`);
      writeJob(id, b, { core: b.core_skills, preferred: b.preferred_skills, nice_to_have: b.nice_to_have_skills }, b.domains || []);
      return res(201, jobView(store.jobs[id]));
    }],
    ["POST", /^\/api\/v1\/jobs\/from-description$|^\/api\/v1\/extract\/jd$/, (m, q, b, path) => {
      if (!b?.text || !String(b.text).trim()) return invalid(["body", "text"], "String should have at least 1 character");
      const createOnly = path.endsWith("from-description");
      const id = ((createOnly ? b.id : b.job_id) && String(createOnly ? b.id : b.job_id).trim()) || uuid();
      if (createOnly && store.jobs[id]) return conflict(`Job '${id}' already exists`);
      const h = hash(b.text);
      if (store.jobs[id]?.source_hash === h) return res(200, { job_id: id, status: "unchanged", extraction_source: "skipped",
        skills_written: null, unmapped_skills: null, unmapped_domains: null, extracted: null });
      const extracted = parseJob(b.text);
      const w = writeJob(id, extracted, { core: extracted.core_skills, preferred: extracted.preferred_skills,
        nice_to_have: extracted.nice_to_have_skills }, extracted.domains, h);
      return res(createOnly ? 201 : 200, { job_id: id, status: w.created ? "created" : "updated", extraction_source: EXTRACTOR,
        skills_written: w.rows.map(r => r.name), unmapped_skills: w.unmapped, unmapped_domains: w.unknownDomains, extracted });
    }],
    ["GET", /^\/api\/v1\/jobs$/, (m, q) => {
      const limit = q.limit == null ? 50 : Number(q.limit);
      if (!(limit >= 1 && limit <= 200)) return invalid(["query", "limit"], "Input should be between 1 and 200");
      return res(200, Object.values(store.jobs).sort((a, b) => a.title.localeCompare(b.title) || a.id.localeCompare(b.id)).slice(0, limit)
        .map(j => ({ id: j.id, title: j.title, company: j.company, seniority: j.seniority, skill_count: Object.keys(j.skills).length, unmapped_count: j.unmapped_skills.length })));
    }],
    ["GET", /^\/api\/v1\/jobs\/([^/]+)$/, m => store.jobs[m[1]] ? res(200, jobView(store.jobs[m[1]])) : notFound(`Job '${m[1]}' not found`)],
    ["DELETE", /^\/api\/v1\/jobs\/([^/]+)$/, m => {
      if (!store.jobs[m[1]]) return notFound(`Job '${m[1]}' not found`);
      delete store.jobs[m[1]]; save(); return res(204, "");
    }],
    ["GET", /^\/api\/v1\/analyze\/([^/]+)\/([^/]+)$/, (m, q) => analyze(m[1], m[2], String(q.explain ?? "true") !== "false")],
    ["POST", /^\/api\/v1\/pivot$/, (m, q, b) => {
      if (!b?.user_id) return invalid(["body", "user_id"], "Field required");
      if (!b?.target_role) return invalid(["body", "target_role"], "Field required");
      const u = store.users[b.user_id]; if (!u) return notFound(`User '${b.user_id}' not found`);
      const j = b.job_id ? store.jobs[b.job_id] : null; if (b.job_id && !j) return notFound(`Job '${b.job_id}' not found`);
      const role = D.roles.find(r => r.role_title.toLowerCase() === String(b.target_role).trim().toLowerCase());
      if (!role) return res(404, { detail: `The sandbox only knows these roles: ${D.roles.map(r => r.role_title).join(", ")}. The real API asks Gemini for any role.` });
      const userSkills = Object.fromEntries(Object.entries(u.skills).map(([n, e]) => [n, e.proficiency]));
      const jdSkills = j ? Object.fromEntries(Object.entries(j.skills).map(([n, e]) => [n, e.weight])) : {};
      return res(200, { ...NN.scorePivot(u.id, role, userSkills, jdSkills), extraction_source: "sandbox-role-template", unmapped_role_skills: [] });
    }],
  ];

  async function request(method, path, { query = {}, body } = {}) {
    await new Promise(r => setTimeout(r, 60 + Math.random() * 90)); // feel like a network call
    const clean = path.split("?")[0];
    for (const [verb, re, handler] of ROUTES) {
      const m = clean.match(re);
      if (verb === method && m) {
        const params = m.map(x => (x === undefined ? x : decodeURIComponent(x)));
        return handler(params, query, body, clean);
      }
    }
    return res(404, { detail: "Not Found" });
  }

  const SAMPLE_USER = { id: "dash_user", name: "Dashboard Tester", target_domain: "nlp" };
  const SAMPLE_SKILLS = [{ name: "python", proficiency: 0.9 }, { name: "torch", proficiency: 0.5 },
    { name: "Elasticsearch", proficiency: 0.8 }, { name: "k8s", proficiency: 0.4 }, { name: "Basket Weaving", proficiency: 0.7 }];
  const SAMPLE_JOB = { id: "dash_job", title: "Applied ML Engineer", company: "Acme AI", seniority: "mid", domains: ["nlp"],
    core_skills: [{ name: "Python", weight: 0.7 }, { name: "PyTorch", weight: 0.7 }, { name: "LLM fine-tuning", weight: 0.6 }],
    preferred_skills: [{ name: "vector db", weight: 0.5 }, { name: "RAG", weight: 0.5 }],
    nice_to_have_skills: [{ name: "Docker", weight: 0.4 }] };

  window.NNMock = {
    request,
    counts: () => ({ users: Object.keys(store.users).length, jobs: Object.keys(store.jobs).length }),
    reset() { store = { users: {}, jobs: {} }; save(); },
    async loadSample() {
      store.users[SAMPLE_USER.id] = { ...SAMPLE_USER, skills: {}, unmapped_skills: [] };
      await request("PUT", `/api/v1/users/${SAMPLE_USER.id}/skills`, { body: { skills: SAMPLE_SKILLS } });
      delete store.jobs[SAMPLE_JOB.id];
      await request("POST", "/api/v1/jobs", { body: SAMPLE_JOB });
    },
  };
})();
