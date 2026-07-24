"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";

/**
 * Guarded Autopilot (M4.4, opt-in, off by default). Enabling requires an
 * admin-scoped key — the dashboard's key is read+operate, so Enable will 403
 * here unless the operator has wired an admin key. That friction is the design,
 * not a bug. The kill switch works with the normal operate scope, instantly.
 */
export function AutopilotPanel({
  campaignId,
  enabled,
  schedulingUrl,
  infoDocUrl,
}: {
  campaignId: string;
  enabled: boolean;
  schedulingUrl: string | null | undefined;
  infoDocUrl: string | null | undefined;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function toggle(action: "enable" | "disable") {
    setError(null);
    startTransition(async () => {
      try {
        if (action === "enable") await api.enableAutopilot(campaignId);
        else await api.disableAutopilot(campaignId);
        router.refresh();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setError(
          msg.includes("403")
            ? "Enabling requires an admin-scoped API key — deliberate friction. " +
              "Run: POST /campaigns/{id}/autopilot/enable with an admin key."
            : msg,
        );
      }
    });
  }

  return (
    <section className="rounded-[var(--radius)] border border-line bg-surface p-6 shadow-[var(--shadow)]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h2 className="font-[family-name:var(--font-display)] text-lg text-ink">
            Guarded Autopilot
          </h2>
          <Badge tone={enabled ? "warn" : "muted"}>
            {enabled ? "ON — auto-sending" : "off"}
          </Badge>
        </div>
        {enabled ? (
          <button
            type="button"
            disabled={pending}
            onClick={() => toggle("disable")}
            className="rounded-lg border border-warn bg-warn-soft px-3.5 py-1.5 text-xs font-semibold text-warn transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            Kill switch — disable now
          </button>
        ) : (
          <button
            type="button"
            disabled={pending}
            onClick={() => toggle("enable")}
            className="rounded-lg border border-line bg-bg px-3.5 py-1.5 text-xs font-medium text-muted transition-colors hover:border-ink hover:text-ink disabled:opacity-50"
          >
            Enable (admin key required)
          </button>
        )}
      </div>
      <p className="mt-3 text-xs leading-relaxed text-muted">
        Off by default. When enabled, Craftsman may auto-send a <b>validated,
        template-constrained</b> reply for exactly three deterministic intents —
        interested → your booking link, &quot;send me info&quot; → your one-pager,
        timing objection → a follow-up offer — at classifier confidence ≥ 0.9,
        inside business hours, with no escalation match, and <b>at most one
        auto-reply per thread, ever</b> (hardcoded). Everything else stays a draft
        for you. Auto-sent replies are badged in the inbox and audit-logged.
      </p>
      {!schedulingUrl || !infoDocUrl ? (
        <p className="mt-2 text-[11px] text-faint">
          {!schedulingUrl ? "No booking link set — interested replies will always escalate. " : ""}
          {!infoDocUrl ? "No one-pager link set — info requests will always escalate." : ""}
        </p>
      ) : null}
      {error ? <p className="mt-2 text-xs text-warn">{error}</p> : null}
    </section>
  );
}
