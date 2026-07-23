"use client";

import { useRouter, useSearchParams } from "next/navigation";

const STATUSES = ["all", "new", "verified", "disqualified", "suppressed"] as const;

/** Filters drive the URL, so the server component re-queries and the view is shareable. */
export function LeadFilters() {
  const router = useRouter();
  const params = useSearchParams();
  const status = params.get("status") ?? "all";
  const scoreGte = params.get("score_gte") ?? "";

  function apply(next: { status?: string; score_gte?: string }) {
    const q = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(next)) {
      if (!value || value === "all") q.delete(key);
      else q.set(key, value);
    }
    const qs = q.toString();
    router.push(qs ? `/leads?${qs}` : "/leads");
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="flex flex-wrap gap-1.5">
        {STATUSES.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => apply({ status: s })}
            className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide transition-colors ${
              status === s ? "bg-ink text-white" : "bg-bg text-muted hover:text-ink"
            }`}
          >
            {s}
          </button>
        ))}
      </div>
      <label className="flex items-center gap-2 text-xs text-muted">
        Min ICP
        <input
          type="number"
          step="0.05"
          min={0}
          max={1}
          defaultValue={scoreGte}
          onBlur={(e) => apply({ score_gte: e.target.value })}
          onKeyDown={(e) => {
            if (e.key === "Enter") apply({ score_gte: e.currentTarget.value });
          }}
          placeholder="—"
          className="w-20 rounded-lg border border-line bg-bg px-2 py-1 text-sm text-ink outline-none focus:border-ink"
        />
      </label>
    </div>
  );
}
