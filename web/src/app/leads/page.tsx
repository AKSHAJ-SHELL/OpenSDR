import Link from "next/link";
import { Suspense } from "react";
import { api } from "@/lib/api";
import { LeadActions } from "@/components/leads/LeadActions";
import { LeadFilters } from "@/components/leads/LeadFilters";
import { LeadImport } from "@/components/leads/LeadImport";
import { ScoreCell } from "@/components/leads/ScoreCell";
import { SourceCell } from "@/components/leads/SourceCell";
import { ApiDown } from "@/components/ui/ApiDown";
import { Badge, statusTone } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";

export const dynamic = "force-dynamic";

const SUBTITLE = "ICP-scored prospects. Only verified addresses enroll.";

export default async function LeadsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; score_gte?: string }>;
}) {
  const { status, score_gte } = await searchParams;
  const scoreGte = score_gte ? Number(score_gte) : undefined;

  let leads;
  let weights;
  try {
    [leads, weights] = await Promise.all([
      api.leads({
        status: status && status !== "all" ? status : undefined,
        score_gte: Number.isFinite(scoreGte) ? scoreGte : undefined,
      }),
      api.scoringWeights().catch(() => undefined),
    ]);
  } catch (e) {
    return (
      <>
        <PageHeader title="Leads" subtitle={SUBTITLE} />
        <ApiDown error={e instanceof Error ? e.message : String(e)} />
      </>
    );
  }

  return (
    <>
      <PageHeader title="Leads" subtitle={SUBTITLE} />

      <div className="grid gap-5 animate-rise-delay-1">
        <LeadImport />

        <Suspense fallback={null}>
          <LeadFilters />
        </Suspense>

        {leads.length === 0 ? (
          <EmptyState
            title="No leads match"
            body="Import a CSV above, or clear the filters. New leads queue for MX verification before they can enroll."
          />
        ) : (
          <div className="overflow-visible rounded-[var(--radius)] border border-line bg-surface shadow-[var(--shadow)]">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-line bg-bg/70 text-[11px] uppercase tracking-[0.08em] text-faint">
                <tr>
                  <th className="px-5 py-3 font-semibold">Lead</th>
                  <th className="px-5 py-3 font-semibold">Title</th>
                  <th className="px-5 py-3 font-semibold">ICP</th>
                  <th className="px-5 py-3 font-semibold">Source</th>
                  <th className="px-5 py-3 font-semibold">Status</th>
                  <th className="px-5 py-3 font-semibold">Verified</th>
                  <th className="px-5 py-3 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {leads.map((lead) => {
                  const name = [lead.first_name, lead.last_name].filter(Boolean).join(" ");
                  return (
                    <tr
                      key={lead.id}
                      className="border-b border-line last:border-0 transition-colors hover:bg-bg/80"
                    >
                      <td className="px-5 py-3.5">
                        <Link href={`/leads/${lead.id}`} className="group block">
                          <div className="font-semibold text-ink underline-offset-2 group-hover:underline">
                            {name || lead.email}
                          </div>
                          <div className="text-xs text-muted">{lead.email}</div>
                        </Link>
                      </td>
                      <td className="px-5 py-3.5 text-muted">{lead.title || "—"}</td>
                      <td className="px-5 py-3.5">
                        <ScoreCell lead={lead} weights={weights} />
                      </td>
                      <td className="px-5 py-3.5">
                        <SourceCell lead={lead} />
                      </td>
                      <td className="px-5 py-3.5">
                        <Badge tone={statusTone(lead.status)}>{lead.status}</Badge>
                      </td>
                      <td className="px-5 py-3.5">
                        <Badge tone={lead.email_verified ? "good" : "muted"}>
                          {lead.email_verified ? "yes" : "no"}
                        </Badge>
                      </td>
                      <td className="px-5 py-3.5">
                        <LeadActions lead={lead} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
