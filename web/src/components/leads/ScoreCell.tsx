"use client";

import { useState } from "react";
import type { Lead } from "@/lib/types";

const COSINE_WEIGHT = 0.7;
const RULE_WEIGHT = 0.3;

/**
 * ICP score with a breakdown popover. The components come from the server
 * (leads.icp_cosine / icp_rule, recorded at scoring time) — never re-derived here,
 * so the explanation stays truthful if the weights change.
 */
export function ScoreCell({ lead }: { lead: Lead }) {
  const [open, setOpen] = useState(false);
  const score = lead.icp_score;

  if (score == null) {
    return <span className="text-xs text-faint">not scored</span>;
  }

  const pct = Math.round(Math.min(1, Math.max(0, score)) * 100);
  const hasBreakdown = lead.icp_cosine != null && lead.icp_rule != null;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 rounded px-1 py-0.5 text-left transition-colors hover:bg-bg"
        title="Explain this score"
      >
        <div className="h-1.5 w-20 overflow-hidden rounded-full bg-bg">
          <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
        </div>
        <span className="tabular-nums text-xs font-medium text-muted">{score.toFixed(2)}</span>
      </button>

      {open ? (
        <div className="absolute left-0 top-8 z-20 w-72 rounded-[var(--radius)] border border-line bg-surface p-4 text-xs shadow-[var(--shadow)]">
          {hasBreakdown ? (
            <>
              <div className="font-semibold text-ink">How this score was built</div>
              <table className="mt-2 w-full">
                <tbody className="text-muted">
                  <tr>
                    <td className="py-0.5">Similarity to ICP text</td>
                    <td className="text-right tabular-nums text-ink">
                      {lead.icp_cosine!.toFixed(2)} × {COSINE_WEIGHT}
                    </td>
                  </tr>
                  <tr>
                    <td className="py-0.5">
                      Seniority rule
                      {lead.icp_matched_keyword ? (
                        <span className="text-faint"> (&ldquo;{lead.icp_matched_keyword}&rdquo;)</span>
                      ) : (
                        <span className="text-faint"> (no title)</span>
                      )}
                    </td>
                    <td className="text-right tabular-nums text-ink">
                      {lead.icp_rule!.toFixed(2)} × {RULE_WEIGHT}
                    </td>
                  </tr>
                  <tr className="border-t border-line">
                    <td className="pt-1 font-semibold text-ink">Total</td>
                    <td className="pt-1 text-right font-semibold tabular-nums text-ink">
                      {score.toFixed(2)}
                    </td>
                  </tr>
                </tbody>
              </table>
              <p className="mt-2 text-faint">
                Scored against{" "}
                <span className="text-muted">
                  {lead.icp_scored_campaign_name ?? "a deleted campaign"}
                </span>
                {lead.icp_scored_at
                  ? ` on ${new Date(lead.icp_scored_at).toLocaleDateString()}`
                  : ""}
                . Every activation re-scores leads against that campaign&apos;s ICP.
              </p>
            </>
          ) : (
            <p className="text-muted">
              This score predates component tracking, so the breakdown is not available.
              Activating a campaign re-scores the lead and records it.
            </p>
          )}
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="mt-3 text-[11px] font-semibold text-faint hover:text-ink"
          >
            Close
          </button>
        </div>
      ) : null}
    </div>
  );
}
