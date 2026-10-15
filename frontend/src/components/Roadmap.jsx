const PRIORITY_STYLE = {
  high:   "border-red-500 bg-red-50 dark:bg-red-900/10",
  medium: "border-amber-500 bg-amber-50 dark:bg-amber-900/10",
  low:    "border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900",
};

const PRIORITY_BADGE = {
  high:   "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400",
  medium: "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400",
  low:    "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400",
};

export default function Roadmap({ roadmap }) {
  return (
    <div className="space-y-3">
      {roadmap.map(item => (
        <div key={item.skill}
          className={`flex gap-4 px-4 py-4 rounded-xl border-l-4 ${PRIORITY_STYLE[item.priority]}`}>
          <div className="w-7 h-7 rounded-full bg-gray-100 dark:bg-gray-800 flex items-center justify-center text-xs font-bold text-gray-500 dark:text-gray-400 flex-shrink-0">
            {item.rank}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-sm font-semibold text-gray-900 dark:text-white">{item.skill}</span>
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${PRIORITY_BADGE[item.priority]}`}>
                {item.priority}
              </span>
            </div>
            <p className="text-xs text-gray-500 dark:text-gray-400">{item.reason}</p>
          </div>
        </div>
      ))}
    </div>
  );
}