import { notFound } from "next/navigation";
import { api } from "@/lib/api";
import { CampaignActions } from "@/components/campaigns/CampaignActions";
import { CampaignBuilder } from "@/components/campaigns/CampaignBuilder";
import { ApiDown } from "@/components/ui/ApiDown";
import { Badge, statusTone } from "@/components/ui/Badge";
import { PageHeader } from "@/components/ui/PageHeader";

export const dynamic = "force-dynamic";

export default async function CampaignDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let campaign;
  try {
    campaign = await api.campaignDetail(id);
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    if (message.startsWith("404")) notFound();
    return (
      <>
        <PageHeader title="Campaign" />
        <ApiDown error={message} />
      </>
    );
  }

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
      <div className="animate-rise-delay-1">
        <CampaignBuilder campaign={campaign} />
      </div>
    </>
  );
}
