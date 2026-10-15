import { useEffect, useState } from "react";
import { onApiError } from "../api/client";

/** App-wide banner for the two free-tier failure modes (and a down API). */
export default function StatusBanner() {
  const [error, setError] = useState(null);

  useEffect(() => onApiError(setError), []);

  if (!error) return null;

  const tone = error.status === 429
    ? "border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/50 text-amber-800 dark:text-amber-200"
    : "border-red-300 dark:border-red-900 bg-red-50 dark:bg-red-950/50 text-red-800 dark:text-red-200";

  const title = {
    429: "Gemini free-tier budget reached",
    503: error.code === "GRAPH_UNAVAILABLE" ? "Neo4j is unreachable" : "Service unavailable",
    0: "API unreachable",
  }[error.status] || "Request failed";

  return (
    <div className={`border-b px-6 py-2.5 text-sm ${tone}`} role="status">
      <div className="max-w-6xl mx-auto flex items-start justify-between gap-4">
        <div>
          <strong className="font-semibold">{title}.</strong>{" "}
          {error.message}
          {error.retryAfter ? ` Retry in ~${error.retryAfter}s.` : ""}
          {error.hint && <div className="text-xs mt-0.5 opacity-90">{error.hint}</div>}
          {error.status === 429 && (
            <div className="text-xs mt-0.5 opacity-90">
              Features that don't use the LLM (structured users and jobs, analysis without
              explanation, ontology) keep working.
            </div>
          )}
        </div>
        <button onClick={() => setError(null)} className="text-xs underline flex-shrink-0">dismiss</button>
      </div>
    </div>
  );
}
