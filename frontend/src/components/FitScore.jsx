export default function FitScore({ score, domainBonus }) {
  const pct = Math.round(score * 100);
  const color =
    pct >= 80 ? "text-emerald-500" :
    pct >= 60 ? "text-amber-500" :
    "text-red-500";
  const ring =
    pct >= 80 ? "stroke-emerald-500" :
    pct >= 60 ? "stroke-amber-500" :
    "stroke-red-500";

  const r = 54;
  const circ = 2 * Math.PI * r;
  const dash = (pct / 100) * circ;

  return (
    <div className="flex flex-col items-center gap-3">
      <div className="relative flex items-center justify-center">
        <svg width="140" height="140" className="-rotate-90">
          <circle cx="70" cy="70" r={r} fill="none"
            className="stroke-gray-200 dark:stroke-gray-700" strokeWidth="10"/>
          <circle cx="70" cy="70" r={r} fill="none"
            className={ring} strokeWidth="10"
            strokeDasharray={`${dash} ${circ}`}
            strokeLinecap="round"
            style={{ transition: "stroke-dasharray 1s ease" }}
          />
        </svg>
        <div className="absolute flex flex-col items-center justify-center">
          <span className={`text-3xl font-bold ${color}`}>{pct}%</span>
          <span className="text-xs text-gray-400 dark:text-gray-500">fit score</span>
        </div>
      </div>
      {domainBonus > 0 && (
        <span className="text-xs text-violet-500 font-medium">
          +{Math.round(domainBonus * 100)}% domain alignment bonus
        </span>
      )}
    </div>
  );
}