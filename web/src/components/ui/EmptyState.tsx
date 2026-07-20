export function EmptyState({
  title,
  body,
}: {
  title: string;
  body: string;
}) {
  return (
    <div className="flex flex-col items-start justify-center rounded-[var(--radius)] border border-dashed border-line bg-surface px-8 py-16">
      <h3 className="font-[family-name:var(--font-display)] text-xl text-ink">{title}</h3>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-muted">{body}</p>
    </div>
  );
}
