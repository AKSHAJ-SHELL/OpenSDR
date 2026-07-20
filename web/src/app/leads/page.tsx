import { api } from "@/lib/api";
import { ApiDown } from "@/components/ui/ApiDown";
import { Badge, statusTone } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";

export const dynamic = "force-dynamic";

function ScoreBar({ score }: { score: number | null }) {
  if (score == null) {
    return <span className="text-xs text-faint">—</span>;
  }
  const pct = Math.round(Math.min(1, Math.max(0, score)) * 100);
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-bg">
        <div
          className="h-full rounded-full bg-accent"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="tabular-nums text-xs font-medium text-muted">
        {score.toFixed(2)}
      </span>
    </div>
  );
}

export default async function LeadsPage() {
  let leads;
  try {
    leads = await api.leads();
  } catch (e) {
    return (
      <>
        <PageHeader
          title="Leads"
          subtitle="ICP-scored prospects. Only verified addresses enroll."
        />
        <ApiDown error={e instanceof Error ? e.message : String(e)} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Leads"
        subtitle="ICP-scored prospects. Only verified addresses enroll."
      />

      {leads.length === 0 ? (
        <EmptyState
          title="No leads yet"
          body="Import a CSV via POST /leads/import, then wait for the worker to verify MX."
        />
      ) : (
        <div className="animate-rise-delay-1 overflow-hidden rounded-[var(--radius)] border border-line bg-surface shadow-[var(--shadow)]">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-line bg-bg/70 text-[11px] uppercase tracking-[0.08em] text-faint">
              <tr>
                <th className="px-5 py-3 font-semibold">Lead</th>
                <th className="px-5 py-3 font-semibold">Title</th>
                <th className="px-5 py-3 font-semibold">ICP</th>
                <th className="px-5 py-3 font-semibold">Status</th>
                <th className="px-5 py-3 font-semibold">Verified</th>
              </tr>
            </thead>
            <tbody>
              {leads.map((lead) => {
                const name = [lead.first_name, lead.last_name]
                  .filter(Boolean)
                  .join(" ");
                return (
                  <tr
                    key={lead.id}
                    className="border-b border-line last:border-0 transition-colors hover:bg-bg/80"
                  >
                    <td className="px-5 py-3.5">
                      <div className="font-semibold text-ink">
                        {name || lead.email}
                      </div>
                      <div className="text-xs text-muted">{lead.email}</div>
                    </td>
                    <td className="px-5 py-3.5 text-muted">
                      {lead.title || "—"}
                    </td>
                    <td className="px-5 py-3.5">
                      <ScoreBar score={lead.icp_score} />
                    </td>
                    <td className="px-5 py-3.5">
                      <Badge tone={statusTone(lead.status)}>{lead.status}</Badge>
                    </td>
                    <td className="px-5 py-3.5">
                      <Badge tone={lead.email_verified ? "good" : "muted"}>
                        {lead.email_verified ? "yes" : "no"}
                      </Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
