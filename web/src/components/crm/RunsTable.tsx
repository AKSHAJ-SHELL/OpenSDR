import { Badge } from "@/components/ui/Badge";
import type { CRMSyncRun } from "@/lib/types";

/** `{imported: 3, deduped: 1}` → `"imported 3 · deduped 1"`. */
export function statsSummary(stats: Record<string, number>): string {
  const parts = Object.entries(stats).map(([k, v]) => `${k} ${v}`);
  return parts.length > 0 ? parts.join(" · ") : "no stats";
}

function fmtDate(iso: string | null): string {
  return iso ? iso.slice(0, 16).replace("T", " ") : "—";
}

function statusTone(status: CRMSyncRun["status"]): "good" | "warn" | "info" {
  if (status === "succeeded") return "good";
  if (status === "failed") return "warn";
  return "info";
}

/** Sync-run history for one connection: inbound imports and outbound pushes. */
export function RunsTable({ runs }: { runs: CRMSyncRun[] }) {
  if (runs.length === 0) {
    return (
      <p className="mt-4 rounded-lg border border-dashed border-line bg-bg px-4 py-6 text-xs text-faint">
        No sync runs yet — commit an import or press “Sync now”.
      </p>
    );
  }

  return (
    <div className="mt-4 overflow-x-auto rounded-lg border border-line bg-bg">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-line text-[11px] uppercase tracking-[0.08em] text-faint">
          <tr>
            <th className="px-4 py-2.5 font-semibold">Direction</th>
            <th className="px-4 py-2.5 font-semibold">Status</th>
            <th className="px-4 py-2.5 font-semibold">Stats</th>
            <th className="px-4 py-2.5 font-semibold">Started</th>
            <th className="px-4 py-2.5 font-semibold">Finished</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id} className="border-b border-line last:border-0">
              <td className="px-4 py-2.5">
                <Badge tone={run.direction === "inbound" ? "info" : "accent"}>
                  {run.direction}
                </Badge>
              </td>
              <td className="px-4 py-2.5">
                <Badge tone={statusTone(run.status)}>{run.status}</Badge>
              </td>
              <td className="px-4 py-2.5 text-xs text-muted">
                {statsSummary(run.stats)}
                {run.error ? (
                  <span className="ml-2 text-warn">{run.error}</span>
                ) : null}
              </td>
              <td className="px-4 py-2.5 font-mono text-xs text-muted">
                {fmtDate(run.created_at)}
              </td>
              <td className="px-4 py-2.5 font-mono text-xs text-muted">
                {fmtDate(run.finished_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
