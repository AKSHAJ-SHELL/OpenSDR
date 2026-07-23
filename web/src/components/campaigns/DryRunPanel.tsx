"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DryRun, DryRunItem } from "@/lib/types";

const MAILPIT_URL = process.env.NEXT_PUBLIC_MAILPIT_URL ?? "http://localhost:8025";

/**
 * Preflight panel: run the real pipeline for N sample leads (delivered to Mailpit
 * only) and show rendered emails + validator verdicts side by side.
 */
export function DryRunPanel({
  campaignId,
  initialRuns,
  loadError = null,
}: {
  campaignId: string;
  initialRuns: DryRun[];
  /** Set when the run history could not be fetched; the panel says so instead of
   *  pretending there are no runs. */
  loadError?: string | null;
}) {
  const [runs, setRuns] = useState(initialRuns);
  const [n, setN] = useState("3");
  const [error, setError] = useState<string | null>(loadError);
  const latest = runs[0] ?? null;
  const polling = latest?.status === "running";

  useEffect(() => {
    if (!polling) return;
    const timer = setInterval(async () => {
      try {
        const fresh = await api.dryRun(campaignId, latest.id);
        setRuns((rs) => [fresh, ...rs.slice(1)]);
      } catch {
        // transient poll failure — keep trying until the run resolves
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [polling, campaignId, latest?.id]);

  async function start() {
    setError(null);
    try {
      const run = await api.startDryRun(campaignId, Number(n) || 3);
      setRuns((rs) => [run, ...rs]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <section className="rounded-[var(--radius)] border border-line bg-surface p-6 shadow-[var(--shadow)]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-[family-name:var(--font-display)] text-lg text-ink">Dry run</h2>
          <p className="mt-1 max-w-xl text-sm text-muted">
            Routes the real pipeline — research, variant pick, slot-fill, validator — for
            your top-scoring sample leads, and delivers to{" "}
            <a href={MAILPIT_URL} target="_blank" rel="noreferrer" className="underline">
              Mailpit
            </a>{" "}
            only, never a real inbox. Nothing is enrolled, counted, or sent for real.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-muted">
            Sample
            <input
              type="number"
              min={1}
              max={10}
              value={n}
              onChange={(e) => setN(e.target.value)}
              className="w-14 rounded-lg border border-line bg-bg px-2 py-1 text-sm text-ink outline-none focus:border-ink"
            />
            leads
          </label>
          <button
            type="button"
            disabled={polling}
            onClick={start}
            className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {polling ? "Running…" : "Run dry-run"}
          </button>
        </div>
      </div>
      {error ? <p className="mt-2 text-xs font-medium text-red-600">{error}</p> : null}

      {latest ? (
        <div className="mt-4">
          <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
            Latest run · {latest.status}
            {latest.error ? ` — ${latest.error}` : ""}
          </div>
          <div className="mt-2 grid gap-3">
            {latest.items.map((item) => (
              <DryRunItemCard key={item.id} item={item} />
            ))}
            {latest.status === "running" && latest.items.length === 0 ? (
              <p className="text-xs text-faint">Researching and generating…</p>
            ) : null}
          </div>
        </div>
      ) : (
        <p className="mt-4 text-xs text-faint">
          No dry-runs yet. Run one before activating — it&apos;s the cheapest way to catch
          a bad skeleton or a thin ICP.
        </p>
      )}
    </section>
  );
}

function DryRunItemCard({ item }: { item: DryRunItem }) {
  const verdict = item.error
    ? { label: "ERROR", cls: "text-red-600 border-red-600/40" }
    : item.validator_ok
      ? { label: "PASS", cls: "text-emerald-600 border-emerald-600/40" }
      : { label: "REJECTED", cls: "text-amber-600 border-amber-600/40" };

  return (
    <div className="rounded-lg border border-line bg-bg p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${verdict.cls}`}>
          {verdict.label}
        </span>
        <span className="text-sm font-semibold text-ink">{item.lead_name ?? item.lead_email}</span>
        <span className="text-[11px] text-faint">
          {item.lead_email}
          {item.icp_score != null ? ` · ICP ${item.icp_score.toFixed(2)}` : ""}
          {item.variant_name ? ` · variant ${item.variant_name}` : ""}
          {item.delivered ? " · in Mailpit" : ""}
        </span>
      </div>
      {item.error ? (
        <p className="mt-2 text-xs text-red-600">{item.error}</p>
      ) : item.validator_ok ? (
        <pre className="mt-3 whitespace-pre-wrap rounded-lg border border-dashed border-line px-3 py-2 font-mono text-xs leading-relaxed text-muted">
          {`Subject: ${item.subject ?? ""}\n\n${item.body ?? ""}`}
        </pre>
      ) : (
        <ul className="mt-2 grid gap-1">
          {(item.validator_errors ?? []).map((e) => (
            <li key={e} className="text-xs text-amber-600">
              {e}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
