const EFFORT_STYLE = {
  low:    { bar: "bg-emerald-500", text: "text-emerald-500", label: "Low effort" },
  medium: { bar: "bg-amber-500",   text: "text-amber-500",   label: "Medium effort" },
  high:   { bar: "bg-red-500",     text: "text-red-500",     label: "High effort" },
};

export default function PivotAnalysis({ pivot }) {
  if (!pivot) return null;

  const pct = Math.round(pivot.pivot_score * 100);
  const style = EFFORT_STYLE[pivot.effort];

  return (
    <div className="rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 p-6 space-y-6">

      {/* header */}
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs text-gray-400 dark:text-gray-500 uppercase tracking-widest mb-1">
            Pivot target
          </p>
          <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
            {pivot.target_role}
          </h3>
        </div>
        <div className="text-right">
          <p className={`text-2xl font-bold ${style.text}`}>{pct}%</p>
          <p className="text-xs text-gray-400 dark:text-gray-500">readiness</p>
        </div>
      </div>

      {/* effort bar */}
      <div>
        <div className="flex justify-between text-xs mb-1.5">
          <span className="text-gray-500 dark:text-gray-400">Pivot effort</span>
          <span className={`font-medium ${style.text}`}>{style.label}</span>
        </div>
        <div className="h-2 rounded-full bg-gray-100 dark:bg-gray-800">
          <div
            className={`h-2 rounded-full ${style.bar} transition-all duration-700`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <p className="text-xs text-gray-400 dark:text-gray-500 mt-1.5">
          {pivot.effort_label}
        </p>
      </div>

      {/* transferable skills */}
      {pivot.transferable_skills.length > 0 && (
        <div>
          <p className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-widest mb-2">
            Skills that transfer
          </p>
          <div className="flex flex-wrap gap-2">
            {pivot.transferable_skills.map(s => (
              <span key={s}
                className="text-xs px-2.5 py-1 rounded-full bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400 font-medium">
                {s}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* top gaps */}
      {pivot.skill_gaps.length > 0 && (
        <div>
          <p className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-widest mb-2">
            Top skills to acquire
          </p>
          <div className="space-y-2">
            {pivot.skill_gaps.map(g => (
              <div key={g.skill}
                className="flex items-center justify-between px-3 py-2.5 rounded-lg bg-gray-50 dark:bg-gray-800">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-gray-900 dark:text-white">
                    {g.skill}
                  </span>
                  <span className="text-xs text-gray-400 dark:text-gray-500">
                    {g.importance}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-16 h-1.5 rounded-full bg-gray-200 dark:bg-gray-700">
                    <div
                      className="h-1.5 rounded-full bg-violet-500"
                      style={{ width: `${g.transferability * 100}%` }}
                    />
                  </div>
                  <span className="text-xs text-gray-400 dark:text-gray-500 w-20 text-right">
                    {Math.round(g.transferability * 100)}% transfers
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}