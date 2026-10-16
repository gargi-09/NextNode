const STATUS_STYLE = {
  met:     "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400",
  partial: "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400",
  missing: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400",
};

const IMPORTANCE_STYLE = {
  core:         "bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-400",
  preferred:    "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400",
  nice_to_have: "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400",
};

export default function SkillBreakdown({ skills }) {
  return (
    <div className="space-y-2">
      {skills.map(s => (
        <div key={s.skill}
          className="flex items-center justify-between px-4 py-3 rounded-xl bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-800">
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium text-gray-900 dark:text-white">{s.skill}</span>
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${IMPORTANCE_STYLE[s.importance]}`}>
              {s.importance.replaceAll("_", " ")}
            </span>
          </div>
          <div className="flex items-center gap-3">
            {s.prereqs_owned > 0 && s.status !== "met" && (
              <span className="text-xs text-gray-400 dark:text-gray-500">
                {s.prereqs_owned} prereq{s.prereqs_owned > 1 ? "s" : ""}
              </span>
            )}
            <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_STYLE[s.status]}`}>
              {s.status}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}