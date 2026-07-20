import { apiBase } from "@/lib/api";

export function ApiDown({ error }: { error?: string }) {
  return (
    <div className="rounded-[var(--radius)] border border-warn/30 bg-warn-soft px-6 py-5 text-sm text-warn animate-rise">
      <div className="font-semibold">Can&apos;t reach the Craftsman API</div>
      <p className="mt-1 text-warn/90">
        Expected at <code className="font-mono">{apiBase()}</code>. Start uvicorn,
        then refresh.
      </p>
      {error ? (
        <p className="mt-2 font-mono text-xs opacity-80">{error}</p>
      ) : null}
    </div>
  );
}
