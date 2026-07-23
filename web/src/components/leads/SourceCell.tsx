"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { Lead, LeadEnrichment } from "@/lib/types";

/**
 * Lead source with an enrichment-provenance popover. Provenance is fetched on
 * open (not with the list — one request per inspection, not per row) and shows
 * which provider said what, even where the CSV value won: honest labeling of
 * where every field came from is the whole point of BYO-key enrichment.
 */
export function SourceCell({ lead }: { lead: Lead }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<LeadEnrichment[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && rows === null) {
      try {
        setRows(await api.leadEnrichments(lead.id));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    }
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={toggle}
        className="rounded px-1 py-0.5 text-left text-xs text-muted transition-colors hover:bg-bg"
        title="Show enrichment provenance"
      >
        {lead.source || "—"}
      </button>

      {open ? (
        <div className="absolute left-0 top-8 z-20 w-80 rounded-[var(--radius)] border border-line bg-surface p-4 text-xs shadow-[var(--shadow)]">
          <div className="font-semibold text-ink">Enrichment provenance</div>
          {error ? (
            <p className="mt-2 text-muted">Couldn&rsquo;t load provenance: {error}</p>
          ) : rows === null ? (
            <p className="mt-2 text-faint">Loading…</p>
          ) : rows.length === 0 ? (
            <p className="mt-2 text-muted">
              No enrichment recorded. Configure provider keys (
              <code>ENRICHMENT_PROVIDERS</code>) to enrich verified leads from your own
              provider accounts.
            </p>
          ) : (
            <table className="mt-2 w-full">
              <tbody className="text-muted">
                {rows.map((r) => (
                  <tr key={`${r.field}-${r.fetched_at}`}>
                    <td className="py-0.5 pr-2 align-top text-faint">{r.field}</td>
                    <td className="py-0.5 pr-2 align-top break-all text-ink">{r.value}</td>
                    <td className="py-0.5 whitespace-nowrap text-right align-top">
                      {r.source} · {r.confidence.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : null}
    </div>
  );
}
