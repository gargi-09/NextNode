import { useId } from "react";
import { Button, Input } from "./ui";

/**
 * Editable list of {name, <valueKey>} rows with ontology autocomplete.
 * valueKey is "proficiency" for users and "weight" for job requirements.
 */
export default function SkillRowsEditor({ rows, onChange, valueKey, valueLabel, suggestions = [] }) {
  const listId = useId();
  const canonical = new Set(suggestions.map(s => s.toLowerCase()));

  const update = (i, patch) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const remove = i => onChange(rows.filter((_, j) => j !== i));
  const add = () => onChange([...rows, { name: "", [valueKey]: 0.6 }]);

  return (
    <div className="space-y-2">
      <datalist id={listId}>
        {suggestions.map(s => <option key={s} value={s} />)}
      </datalist>
      {rows.map((row, i) => {
        const unknown = row.name.trim() && suggestions.length > 0 && !canonical.has(row.name.trim().toLowerCase());
        return (
          <div key={i} className="flex items-center gap-2">
            <div className="flex-1 min-w-0">
              <Input
                list={listId}
                value={row.name}
                placeholder="Skill name"
                onChange={e => update(i, { name: e.target.value })}
                aria-label="Skill name"
              />
              {unknown && (
                <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-0.5">
                  Not a canonical name; kept only if it's a known alias, otherwise stored as unmapped.
                </p>
              )}
            </div>
            <input
              type="range" min="0" max="1" step="0.05"
              value={row[valueKey]}
              onChange={e => update(i, { [valueKey]: Number(e.target.value) })}
              className="w-28 accent-violet-600"
              aria-label={valueLabel}
            />
            <span className="w-10 text-right text-xs tabular-nums text-gray-600 dark:text-gray-300">
              {Math.round(row[valueKey] * 100)}%
            </span>
            <button onClick={() => remove(i)} className="text-gray-400 hover:text-red-500 px-1" aria-label="Remove skill">✕</button>
          </div>
        );
      })}
      <Button variant="ghost" onClick={add} type="button">+ Add skill</Button>
    </div>
  );
}
