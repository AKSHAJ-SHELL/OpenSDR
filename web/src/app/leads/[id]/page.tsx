import Link from "next/link";
import { api } from "@/lib/api";
import type { TimelineItem } from "@/lib/types";
import { ApiDown } from "@/components/ui/ApiDown";
import { Badge, statusTone } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";

export const dynamic = "force-dynamic";

function itemTone(item: TimelineItem): "accent" | "good" | "warn" | "info" | "muted" {
  if (item.kind === "reply") return "good";
  if (item.kind === "task") return item.status === "done" ? "accent" : "info";
  return "muted";
}

function itemLabel(item: TimelineItem): string {
  if (item.kind === "email_sent") return "email sent";
  if (item.kind === "reply") return "reply";
  if (item.channel === "linkedin_task") return "linkedin";
  if (item.channel === "call_task") return "call";
  return item.kind;
}

export default async function LeadDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let lead;
  let timeline;
  try {
    [lead, timeline] = await Promise.all([api.lead(id), api.leadTimeline(id)]);
  } catch (e) {
    return (
      <>
        <PageHeader title="Lead" subtitle="Touch history" />
        <ApiDown error={e instanceof Error ? e.message : String(e)} />
      </>
    );
  }

  const name = [lead.first_name, lead.last_name].filter(Boolean).join(" ") || lead.email;

  return (
    <>
      <PageHeader
        title={name}
        subtitle="Every contact this system has had with this person — autonomous emails and human touches alike."
      />

      <div className="grid gap-5 animate-rise-delay-1">
        <div className="flex flex-wrap items-center gap-3 rounded-[var(--radius)] border border-line bg-surface px-5 py-4 shadow-[var(--shadow)] text-sm">
          <span className="text-muted">{lead.email}</span>
          {lead.title ? <span className="text-muted">· {lead.title}</span> : null}
          <Badge tone={statusTone(lead.status)}>{lead.status}</Badge>
          {lead.icp_score != null ? (
            <span className="text-xs text-faint">ICP {lead.icp_score.toFixed(2)}</span>
          ) : null}
          <span className="ml-auto">
            <Link
              href="/leads"
              className="text-xs font-semibold text-muted underline-offset-2 hover:text-ink hover:underline"
            >
              ← All leads
            </Link>
          </span>
        </div>

        {timeline.length === 0 ? (
          <EmptyState
            title="No touches yet"
            body="Once this lead is enrolled in a campaign, every email send, reply, and human-touch task lands here as one unified history."
          />
        ) : (
          <ol className="grid gap-0 rounded-[var(--radius)] border border-line bg-surface shadow-[var(--shadow)]">
            {timeline.map((item, i) => (
              <li
                key={`${item.kind}-${item.at}-${i}`}
                className="flex flex-wrap items-baseline gap-2 border-b border-line px-5 py-3.5 text-sm last:border-0"
              >
                <span className="w-40 shrink-0 text-xs text-faint">
                  {new Date(item.at).toLocaleString(undefined, {
                    month: "short",
                    day: "numeric",
                    hour: "numeric",
                    minute: "2-digit",
                  })}
                </span>
                <Badge tone={itemTone(item)}>{itemLabel(item)}</Badge>
                <span className="font-medium text-ink">{item.title}</span>
                {item.classification ? (
                  <Badge tone={statusTone(item.classification)}>{item.classification}</Badge>
                ) : null}
                {item.outcome ? <span className="text-xs text-muted">{item.outcome}</span> : null}
                {item.detail ? <span className="text-xs text-faint">{item.detail}</span> : null}
                {item.campaign_name ? (
                  <span className="ml-auto text-xs text-faint">{item.campaign_name}</span>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </div>
    </>
  );
}
