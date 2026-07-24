"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import type { Campaign, PlacementRun, PlacementVerdict } from "@/lib/types";

const inputCls =
  "w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-ink";
const labelCls =
  "block text-[11px] font-semibold uppercase tracking-[0.08em] text-faint";

const MARKS: Exclude<PlacementVerdict, "pending">[] = ["inbox", "spam", "missing"];

function verdictTone(v: PlacementVerdict): "good" | "warn" | "muted" | "accent" {
  if (v === "inbox") return "good";
  if (v === "spam") return "warn";
  if (v === "missing") return "accent";
  return "muted";
}

function runSummary(run: PlacementRun): string {
  const marked = run.results.filter((r) => r.verdict !== "pending");
  if (run.status === "failed") return run.error ?? "failed";
  if (marked.length === 0) {
    return run.status === "running" ? "sending…" : "delivered — mark each seed below";
  }
  const inbox = marked.filter((r) => r.verdict === "inbox").length;
  return `${inbox}/${run.results.length} in the inbox`;
}

/** Start a placement run (campaign + ≤10 seed addresses) and mark results inline.
 *  Seeds are operator-owned accounts — you check them yourself and record the
 *  verdict here; there is no proprietary seed network behind this. */
export function PlacementCard({
  campaigns,
  runs,
}: {
  campaigns: Campaign[];
  runs: PlacementRun[];
}) {
  const router = useRouter();
  const [campaignId, setCampaignId] = useState(campaigns[0]?.id ?? "");
  const [seedText, setSeedText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const seeds = seedText
    .split(/[\n,;]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const tooMany = seeds.length > 10;
  const campaignName = new Map(campaigns.map((c) => [c.id, c.name]));

  function start(e: React.FormEvent) {
    e.preventDefault();
    if (!campaignId || seeds.length === 0 || tooMany) return;
    setError(null);
    startTransition(async () => {
      try {
        await api.startPlacement(campaignId, seeds);
        setSeedText("");
        router.refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    });
  }

  function mark(runId: string, seed: string, verdict: Exclude<PlacementVerdict, "pending">) {
    setError(null);
    startTransition(async () => {
      try {
        await api.markPlacement(runId, { [seed]: verdict });
        router.refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    });
  }

  return (
    <div className="rounded-[var(--radius)] border border-line bg-surface p-6 shadow-[var(--shadow)]">
      <h2 className="font-[family-name:var(--font-display)] text-base text-ink">
        Inbox placement smoke test
      </h2>
      <p className="mt-1 text-xs leading-relaxed text-muted">
        Sends the campaign’s opener (with sample fills, no LLM) to up to 10 seed
        addresses <span className="font-semibold">you own</span>, through your real
        mailboxes. Check each seed inbox yourself, then mark it inbox, spam, or missing —
        there’s no proprietary seed network behind this, and that’s the point.
      </p>

      <form onSubmit={start} className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="placement-campaign" className={labelCls}>
            Campaign
          </label>
          <select
            id="placement-campaign"
            value={campaignId}
            onChange={(e) => setCampaignId(e.target.value)}
            className={`mt-2 ${inputCls}`}
          >
            {campaigns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="placement-seeds" className={labelCls}>
            Seed addresses (≤10, one per line)
          </label>
          <textarea
            id="placement-seeds"
            rows={3}
            value={seedText}
            onChange={(e) => setSeedText(e.target.value)}
            placeholder={"me@gmail-seed.com\nme@outlook-seed.com"}
            className={`mt-2 ${inputCls} font-mono text-xs`}
          />
        </div>
        <div className="sm:col-span-2 flex items-center gap-3">
          <button
            type="submit"
            disabled={pending || !campaignId || seeds.length === 0 || tooMany}
            className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {pending ? "Working…" : "Start placement run"}
          </button>
          {tooMany ? (
            <span className="text-xs font-medium text-red-600">
              At most 10 seeds per run.
            </span>
          ) : seeds.length > 0 ? (
            <span className="text-xs text-muted">{seeds.length} seed(s)</span>
          ) : null}
        </div>
      </form>

      {error ? <p className="mt-3 text-xs font-medium text-red-600">{error}</p> : null}

      {runs.length === 0 ? (
        <p className="mt-5 border-t border-line pt-4 text-xs text-faint">
          No placement runs yet.
        </p>
      ) : (
        <div className="mt-5 grid gap-4">
          {runs.map((run) => (
            <div key={run.id} className="rounded-lg border border-line bg-bg p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <span className="text-sm font-semibold text-ink">
                    {campaignName.get(run.campaign_id) ?? "deleted campaign"}
                  </span>
                  <span className="ml-2 text-xs text-muted">{runSummary(run)}</span>
                </div>
                <div className="flex items-center gap-2">
                  <Badge
                    tone={
                      run.status === "complete"
                        ? "good"
                        : run.status === "failed"
                          ? "warn"
                          : "info"
                    }
                  >
                    {run.status}
                  </Badge>
                  <span className="text-[11px] text-faint">
                    {new Date(run.created_at).toLocaleString()}
                  </span>
                </div>
              </div>
              <div className="mt-3 grid gap-2">
                {run.results.map((res) => (
                  <div
                    key={res.id}
                    className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2"
                  >
                    <div className="min-w-0">
                      <code className="font-mono text-xs text-ink">{res.seed_email}</code>
                      {res.error ? (
                        <span className="ml-2 text-[11px] text-warn">{res.error}</span>
                      ) : !res.delivered && run.status !== "running" ? (
                        <span className="ml-2 text-[11px] text-faint">not delivered</span>
                      ) : null}
                    </div>
                    <div className="flex items-center gap-1.5">
                      <Badge tone={verdictTone(res.verdict)}>{res.verdict}</Badge>
                      {res.delivered
                        ? MARKS.filter((m) => m !== res.verdict).map((m) => (
                            <button
                              key={m}
                              type="button"
                              disabled={pending}
                              onClick={() => mark(run.id, res.seed_email, m)}
                              className="rounded-md border border-line px-2 py-0.5 text-[11px] font-medium text-muted transition-colors hover:border-ink hover:text-ink disabled:opacity-50"
                            >
                              {m}
                            </button>
                          ))
                        : null}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
