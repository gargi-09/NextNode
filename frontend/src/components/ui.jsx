export function Card({ title, subtitle, actions, children, className = "" }) {
  return (
    <section className={`rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 p-5 ${className}`}>
      {(title || actions) && (
        <div className="flex items-start justify-between gap-3 mb-4">
          <div>
            {title && <h2 className="text-sm font-semibold text-gray-900 dark:text-white">{title}</h2>}
            {subtitle && <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{subtitle}</p>}
          </div>
          {actions && <div className="flex gap-2 flex-shrink-0">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

const BUTTON_STYLES = {
  primary:   "bg-violet-600 hover:bg-violet-500 text-white",
  secondary: "border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800",
  danger:    "border border-red-300 dark:border-red-900 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950",
  ghost:     "text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800",
};

export function Button({ variant = "primary", pending, children, className = "", ...props }) {
  return (
    <button
      {...props}
      disabled={props.disabled || pending}
      className={`inline-flex items-center justify-center gap-2 px-3.5 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${BUTTON_STYLES[variant]} ${className}`}
    >
      {pending && <Spinner />}
      {children}
    </button>
  );
}

export function Spinner() {
  return <span className="w-3.5 h-3.5 rounded-full border-2 border-current border-t-transparent animate-spin" aria-hidden="true" />;
}

const INPUT = "w-full rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100 placeholder-gray-400 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-violet-500";

export function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-gray-600 dark:text-gray-300 mb-1">{label}</span>
      {children}
      {hint && <span className="block text-xs text-gray-400 dark:text-gray-500 mt-1">{hint}</span>}
    </label>
  );
}

export function Input(props) {
  return <input {...props} className={`${INPUT} ${props.className || ""}`} />;
}

export function Select({ children, ...props }) {
  return <select {...props} className={`${INPUT} ${props.className || ""}`}>{children}</select>;
}

export function TextArea(props) {
  return <textarea {...props} className={`${INPUT} resize-y ${props.className || ""}`} />;
}

const BADGE_STYLES = {
  met:          "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300",
  partial:      "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300",
  missing:      "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300",
  core:         "bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300",
  preferred:    "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300",
  nice_to_have: "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300",
  manual:       "bg-sky-100 dark:bg-sky-900/30 text-sky-700 dark:text-sky-300",
  resume:       "bg-fuchsia-100 dark:bg-fuchsia-900/30 text-fuchsia-700 dark:text-fuchsia-300",
  neutral:      "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300",
  warning:      "bg-amber-100 dark:bg-amber-900/30 text-amber-800 dark:text-amber-300",
};

export function Badge({ tone = "neutral", children }) {
  return (
    <span className={`inline-block text-xs px-2 py-0.5 rounded-full font-medium whitespace-nowrap ${BADGE_STYLES[tone] || BADGE_STYLES.neutral}`}>
      {children}
    </span>
  );
}

export function ErrorNote({ error, onDismiss }) {
  if (!error) return null;
  return (
    <div role="alert" className="rounded-lg border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/40 px-3 py-2 text-sm text-red-700 dark:text-red-300">
      <div className="flex justify-between gap-3">
        <span>
          <strong className="font-medium">{error.status ? `${error.status} · ` : ""}</strong>
          {error.message}
          {error.retryAfter ? ` (retry after ${error.retryAfter}s)` : ""}
        </span>
        {onDismiss && <button onClick={onDismiss} className="text-xs underline">dismiss</button>}
      </div>
      {error.hint && <p className="text-xs mt-1 opacity-80">{error.hint}</p>}
    </div>
  );
}

export function Empty({ children }) {
  return <p className="text-sm text-gray-400 dark:text-gray-500 py-6 text-center">{children}</p>;
}

export function Loading({ label = "Loading…" }) {
  return (
    <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400 py-6 justify-center">
      <Spinner /> {label}
    </div>
  );
}

export function JsonToggle({ data, label = "Raw JSON" }) {
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-gray-500 dark:text-gray-400 select-none">{label}</summary>
      <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-800 p-3 text-gray-700 dark:text-gray-300">
        {JSON.stringify(data, null, 2)}
      </pre>
    </details>
  );
}
