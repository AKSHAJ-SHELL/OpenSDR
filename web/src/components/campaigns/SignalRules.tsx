"use client";

import { useEffect, useState, useTransition } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import type { SignalRule } from "@/lib/types";

const SIGNAL_TYPES = ["funding", "leadership_hire", "job_posting", "tech_stack_change"];
const ACTIONS = ["boost_score", "notify", "enroll"];

/**
 * Per-campaign signal rules (M2.3). `enroll` is deliberate autonomy — a matching signal
 * auto-enrolls verified, above-threshold leads (into queued; research/validation still
 * run). It's off until you create it here.
 */
export function SignalRules({ campaignId }: { campaignId: string }) {
  const [rules, setRules] = useState<SignalRule[]>([]);
  const [signalType, setSignalType] = useState(SIGNAL_TYPES[0]);
  const [action, setAction] = useState(ACTIONS[0]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [pending, start] = useTransition();

  useEffect(() => {
    api
      .signalRules(campaignId)
      .then(setRules)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoaded(true));
  }, [campaignId]);

  function add() {
    setError(null);
    start(async () => {
      try {
        const rule = await api.createSignalRule(campaignId, { signal_type: signalType, action });
        setRules((r) => [...r, rule]);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  function remove(id: string) {
    start(async () => {
      try {
        await api.deleteSignalRule(campaignId, id);
        setRules((r) => r.filter((x) => x.id !== id));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  return (
    <div className="rounded-[var(--radius)] border border-line bg-surface p-5 shadow-[var(--shadow)]">
      <h2 className="font-[family-name:var(--font-display)] text-base text-ink">Intent signals</h2>
      <p className="mt-1 text-xs text-muted">
        When an intent signal fires for a lead&rsquo;s company, do something.{" "}
        <span className="font-semibold text-ink">enroll</span> is autonomy — it starts a
        sequence without a human clicking Activate (guarded: verified, above-threshold,
        into queued so research &amp; validation still run). Off until you add it.
      </p>

      <div className="mt-4 flex flex-wrap items-end gap-2">
        <label className="grid gap-1">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-faint">Signal</span>
          <select
            value={signalType}
            onChange={(e) => setSignalType(e.target.value)}
            className="rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink"
          >
            {SIGNAL_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </label>
        <label className="grid gap-1">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-faint">Action</span>
          <select
            value={action}
            onChange={(e) => setAction(e.target.value)}
            className="rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink"
          >
            {ACTIONS.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={add}
          disabled={pending}
          className="rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          Add rule
        </button>
      </div>

      {error ? <p className="mt-3 text-xs font-medium text-red-600">{error}</p> : null}

      <div className="mt-4 grid gap-2">
        {!loaded ? (
          <p className="text-xs text-faint">Loading…</p>
        ) : rules.length === 0 ? (
          <p className="text-xs text-muted">No signal rules. Signals still feed the ICP score; they just won&rsquo;t trigger actions.</p>
        ) : (
          rules.map((rule) => (
            <div
              key={rule.id}
              className="flex items-center justify-between rounded-lg border border-line bg-bg px-3 py-2 text-xs"
            >
              <div className="flex items-center gap-2">
                <span className="font-medium text-ink">{rule.signal_type}</span>
                <span className="text-faint">→</span>
                <Badge tone={rule.action === "enroll" ? "warn" : "info"}>{rule.action}</Badge>
              </div>
              <button
                type="button"
                onClick={() => remove(rule.id)}
                disabled={pending}
                className="text-[11px] font-semibold text-faint hover:text-red-600 disabled:opacity-50"
              >
                Remove
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
