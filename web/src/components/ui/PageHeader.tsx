export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4 animate-rise">
      <div>
        <h1 className="font-[family-name:var(--font-display)] text-[2rem] font-medium tracking-tight text-ink">
          {title}
        </h1>
        {subtitle ? (
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted">{subtitle}</p>
        ) : null}
      </div>
      {action}
    </div>
  );
}
