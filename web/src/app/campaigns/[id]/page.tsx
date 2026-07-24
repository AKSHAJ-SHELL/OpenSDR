import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { CampaignActions } from "@/components/campaigns/CampaignActions";
import { CampaignBuilder } from "@/components/campaigns/CampaignBuilder";
import { DryRunPanel } from "@/components/campaigns/DryRunPanel";
import { SignalRules } from "@/components/campaigns/SignalRules";
import { AutopilotPanel } from "@/components/campaigns/AutopilotPanel";
import { ApiDown } from "@/components/ui/ApiDown";
import { Badge, statusTone } from "@/components/ui/Badge";
import { PageHeader } from "@/components/ui/PageHeader";
import type { DryRun } from "@/lib/types";

export const dynamic = "force-dynamic";

function reason(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export default async function CampaignDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  // Both in flight together, but settled independently: preflight history is a
  // side panel, so losing it must never take the editor down with it.
  const [campaignResult, dryRunResult] = await Promise.allSettled([
    api.campaignDetail(id),
    api.dryRuns(id),
  ]);

  if (campaignResult.status === "rejected") {
    const message = reason(campaignResult.reason);
    // Only a 404 on the campaign itself means "no such campaign". Anything else
    // (API down, auth, 5xx) gets the diagnostic, not a bare not-found page.
    if (message.startsWith("404")) notFound();
    return (
      <>
        <PageHeader title="Campaign" />
        <ApiDown error={message} />
      </>
    );
  }

  const campaign = campaignResult.value;
  const dryRuns: DryRun[] =
    dryRunResult.status === "fulfilled" ? dryRunResult.value : [];
  const dryRunError =
    dryRunResult.status === "rejected" ? reason(dryRunResult.reason) : null;

  // An API serving older code returns the pre-M1.1 campaign shape (no steps).
  // Say so plainly instead of crashing on an undefined array.
  const shapeMismatch = !Array.isArray(campaign.steps);

  return (
    <>
      <PageHeader
        title={campaign.name}
        subtitle="Edit the campaign, its sequence, and the skeletons each variant fills."
        action={
          <div className="flex items-center gap-3">
            <Badge tone={statusTone(campaign.status)}>{campaign.status}</Badge>
            <CampaignActions id={campaign.id} status={campaign.status} />
          </div>
        }
      />
      <div className="grid gap-6 animate-rise-delay-1">
        {shapeMismatch ? (
          <p className="rounded-[var(--radius)] border border-amber-600/40 bg-surface px-4 py-3 text-xs text-amber-700">
            The API response is missing the sequence fields this page needs, so the
            builder is read-only. Your API server is probably running older code than
            the dashboard — restart it (<code>uvicorn craftsman.api.app:app --reload</code>)
            and reload this page.
          </p>
        ) : null}
        <DryRunPanel
          campaignId={campaign.id}
          initialRuns={dryRuns}
          loadError={dryRunError}
        />
        <CampaignBuilder
          campaign={{ ...campaign, steps: campaign.steps ?? [] }}
        />
        <AutopilotPanel
          campaignId={campaign.id}
          enabled={campaign.autopilot_enabled ?? false}
          schedulingUrl={campaign.scheduling_url}
          infoDocUrl={campaign.info_doc_url}
        />
        <SignalRules campaignId={campaign.id} />
      </div>
    </>
  );
}
