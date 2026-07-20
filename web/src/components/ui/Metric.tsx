export function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="rounded-[var(--radius)] border border-line bg-surface p-5 shadow-[var(--shadow)]">
      <div className="text-[12px] font-medium uppercase tracking-[0.08em] text-faint">
        {label}
      </div>
      <div className="mt-2 font-[family-name:var(--font-display)] text-3xl font-medium tracking-tight text-ink">
        {value}
      </div>
      {hint ? <div className="mt-1 text-sm text-muted">{hint}</div> : null}
    </div>
  );
}
