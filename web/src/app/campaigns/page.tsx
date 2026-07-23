import Link from "next/link";
import { api } from "@/lib/api";
import { CampaignActions } from "@/components/campaigns/CampaignActions";
import { ApiDown } from "@/components/ui/ApiDown";
import { Badge, statusTone } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";

export const dynamic = "force-dynamic";

const newCampaignAction = (
  <Link
    href="/campaigns/new"
    className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-ink transition-opacity hover:opacity-90"
  >
    New campaign
  </Link>
);

export default async function CampaignsPage() {
  let campaigns;
  try {
    campaigns = await api.campaigns();
  } catch (e) {
    return (
      <>
        <PageHeader
          title="Campaigns"
          subtitle="Sequences, variants, and activation. The agent only enrolls ICP-fit verified leads."
        />
        <ApiDown error={e instanceof Error ? e.message : String(e)} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Campaigns"
        subtitle="Sequences, variants, and activation. The agent only enrolls ICP-fit verified leads."
        action={newCampaignAction}
      />

      {campaigns.length === 0 ? (
        <EmptyState
          title="No campaigns"
          body="Create one, add a variant per step, then dry-run and activate."
        />
      ) : (
        <div className="grid gap-4 animate-rise-delay-1">
          {campaigns.map((c) => (
            <article
              key={c.id}
              className="rounded-[var(--radius)] border border-line bg-surface p-6 shadow-[var(--shadow)] transition-transform duration-200 hover:-translate-y-0.5"
            >
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="font-[family-name:var(--font-display)] text-xl text-ink">
                      <Link href={`/campaigns/${c.id}`} className="hover:underline">
                        {c.name}
                      </Link>
                    </h2>
                    <Badge tone={statusTone(c.status)}>{c.status}</Badge>
                  </div>
                  {c.icp_description ? (
                    <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">
                      {c.icp_description}
                    </p>
                  ) : null}
                  {c.value_prop ? (
                    <p className="mt-2 text-sm text-ink/80">
                      <span className="text-faint">Value prop · </span>
                      {c.value_prop}
                    </p>
                  ) : null}
                </div>
                <div className="flex flex-col items-end gap-3">
                  <div className="text-xs text-muted">
                    Daily cap{" "}
                    <span className="font-semibold text-ink">{c.daily_cap}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Link
                      href={`/campaigns/${c.id}`}
                      className="rounded-lg border border-line bg-bg px-3 py-1.5 text-xs font-semibold text-ink transition-colors hover:border-ink"
                    >
                      Edit
                    </Link>
                    <CampaignActions id={c.id} status={c.status} />
                  </div>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  );
}
