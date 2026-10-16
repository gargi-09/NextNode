import axios from "axios";

// Relative URLs go through the Vite dev proxy (vite.config.js) → FastAPI on :8000,
// so there is no CORS setup and no hard-coded host.
const API_BASE = import.meta.env.VITE_API_URL || "/api/v1";

/** Server root (no /api/v1), for raw requests such as the Features page testers. */
export const API_ROOT = API_BASE.replace(/\/api\/v1\/?$/, "");

const api = axios.create({
  baseURL: API_BASE,
  timeout: 120000, // LLM-backed calls can take a while
});

/** One error shape for every failure: status, machine code, message, retry hint. */
export class ApiError extends Error {
  constructor({ status, code, message, retryAfter, hint, detail }) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
    this.hint = hint;
    this.detail = detail;
  }
}

function toApiError(err) {
  if (!err.response) {
    return new ApiError({
      status: 0,
      code: "NETWORK",
      message: "Cannot reach the NextNode API. Is uvicorn running on port 8000?",
    });
  }
  const { status, data, headers } = err.response;
  const detail = data?.detail;
  const retryAfter = Number(headers?.["retry-after"]) || detail?.retry_after;

  if (status === 429) {
    return new ApiError({ status, code: detail?.code || "LLM_QUOTA", retryAfter, detail,
      message: `Gemini budget used up (${detail?.reason || "rate limit"}). Retry in ${retryAfter}s.` });
  }
  if (status === 503 && detail?.code === "GRAPH_UNAVAILABLE") {
    return new ApiError({ status, code: detail.code, retryAfter, hint: detail.hint, detail,
      message: "The graph database is unreachable." });
  }
  if (status === 422 && Array.isArray(detail)) {
    const msg = detail.map(d => `${d.loc?.slice(1).join(".")}: ${d.msg}`).join("; ");
    return new ApiError({ status, code: "VALIDATION", message: msg, detail });
  }
  const message = typeof detail === "string" ? detail
    : detail?.message || detail?.code || `Request failed (${status})`;
  return new ApiError({ status, code: detail?.code || `HTTP_${status}`, message, retryAfter, detail });
}

// Global listeners (the status banner) get every quota / outage error.
const listeners = new Set();
export function onApiError(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function handle(promise) {
  return promise.then(r => r.data).catch(err => {
    const apiError = toApiError(err);
    if ([0, 429, 503].includes(apiError.status)) listeners.forEach(fn => fn(apiError));
    throw apiError;
  });
}

// ── users ──
export const listUsers     = () => handle(api.get("/users", { params: { limit: 200 } }));
export const getUser       = id => handle(api.get(`/users/${encodeURIComponent(id)}`));
export const createUser    = body => handle(api.post("/users", body));
export const replaceSkills = (id, skills) =>
  handle(api.put(`/users/${encodeURIComponent(id)}/skills`, { skills }));
export const uploadResume  = (id, text) =>
  handle(api.post(`/users/${encodeURIComponent(id)}/resume`, { text }));
export const deleteUser    = id => handle(api.delete(`/users/${encodeURIComponent(id)}`));

// ── jobs ──
export const listJobs      = () => handle(api.get("/jobs", { params: { limit: 200 } }));
export const getJob        = id => handle(api.get(`/jobs/${encodeURIComponent(id)}`));
export const createJob     = body => handle(api.post("/jobs", body));
export const createJobFromText = (text, id) =>
  handle(api.post("/jobs/from-description", { text, id: id || null }));
export const deleteJob     = id => handle(api.delete(`/jobs/${encodeURIComponent(id)}`));

// ── analysis ──
export const analyzeGap = (userId, jobId, explain = false) =>
  handle(api.get(`/analyze/${encodeURIComponent(userId)}/${encodeURIComponent(jobId)}`,
    { params: { explain } }));
export const analyzePivot = (userId, targetRole, jobId = null) =>
  handle(api.post("/pivot", { user_id: userId, target_role: targetRole, job_id: jobId }));

// ── ontology & health ──
export const getOntologyGraph = () => handle(api.get("/ontology/graph"));
export const getUnmapped      = () => handle(api.get("/ontology/unmapped"));
// /health/graph answers 503 with a useful body when Aura is down — return it either way.
export const getGraphHealth = () =>
  axios.get("/health/graph", { timeout: 30000, validateStatus: s => s === 200 || s === 503 })
    .then(r => r.data)
    .catch(err => { throw toApiError(err); });
