import axios from "axios";
import { API_ROOT } from "../api/client";

/** Initial form values for a feature's `tryIt` inputs. */
export function initialValues(tryIt) {
  return Object.fromEntries((tryIt?.inputs || []).map(i => [i.name, i.default ?? ""]));
}

/** Parse a json-typed input; throws a readable error for the form. */
function parseJson(input, raw) {
  try {
    return JSON.parse(raw);
  } catch {
    throw new Error(`"${input.label}" is not valid JSON`);
  }
}

/** Value of one input, typed: numbers, booleans, parsed JSON, or trimmed text (null if empty). */
function typedValue(input, raw) {
  if (input.type === "json") return parseJson(input, raw);
  if (input.type === "number") return raw === "" ? null : Number(raw);
  if (input.type === "checkbox") return Boolean(raw);
  const text = String(raw).trim();
  return text === "" ? null : text;
}

/** Replace "{{name}}" placeholders in a body template with typed input values. */
function fill(template, values) {
  if (typeof template === "string") {
    const whole = template.match(/^\{\{(\w+)\}\}$/);
    if (whole) return values[whole[1]];
    return template.replace(/\{\{(\w+)\}\}/g, (_, k) => values[k] ?? "");
  }
  if (Array.isArray(template)) return template.map(t => fill(t, values));
  if (template && typeof template === "object") {
    return Object.fromEntries(Object.entries(template).map(([k, v]) => [k, fill(v, values)]));
  }
  return template;
}

/** Turn a declarative `tryIt` spec + form values into a request description. */
export function buildRequest(tryIt, rawValues) {
  const typed = {};
  for (const input of tryIt.inputs || []) typed[input.name] = typedValue(input, rawValues[input.name]);

  let path = tryIt.path;
  const params = {};
  for (const input of tryIt.inputs || []) {
    const value = typed[input.name];
    if (input.in === "path") {
      if (value == null) throw new Error(`"${input.label}" is required`);
      path = path.replace(`{${input.name}}`, encodeURIComponent(value));
    } else if (input.in === "query" && value != null) {
      params[input.name] = value;
    }
  }
  const data = tryIt.body ? fill(tryIt.body, typed) : undefined;
  return { method: tryIt.method, url: `${API_ROOT}${path}`, params, data };
}

/** Send a request and always resolve with status, timing, headers and body (never throw on HTTP errors). */
export async function send(request) {
  const started = performance.now();
  try {
    const res = await axios({ ...request, timeout: 120000, validateStatus: () => true });
    return {
      status: res.status,
      ms: Math.round(performance.now() - started),
      retryAfter: res.headers?.["retry-after"],
      data: res.data,
    };
  } catch (err) {
    return {
      status: 0,
      ms: Math.round(performance.now() - started),
      data: { error: "Cannot reach the API. Is uvicorn running on port 8000?", detail: err.message },
    };
  }
}
