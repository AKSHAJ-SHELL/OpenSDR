import { api } from "@/lib/api";
import { DeliverabilityCard } from "@/components/deliverability/DeliverabilityCard";
import { DomainHealthCard } from "@/components/deliverability/DomainHealthCard";
import { PlacementCard } from "@/components/deliverability/PlacementCard";
import { ApiDown } from "@/components/ui/ApiDown";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import type { Campaign, DeliverabilityReport, DomainHealth, PlacementRun } from "@/lib/types";

export const dynamic = "force-dynamic";

const SUBTITLE =
  "Before you send: prove SPF, DKIM, and DMARC are set, watch each domain's health score, and smoke-test inbox placement with your own seed addresses.";

export default async function DeliverabilityPage() {
  let mailboxes;
  try {
    mailboxes = await api.mailboxes();
  } catch (e) {
    return (
      <>
        <PageHeader title="Deliverability" subtitle={SUBTITLE} />
        <ApiDown error={e instanceof Error ? e.message : String(e)} />
      </>
    );
  }

  // Per-domain health (live DNS + DNSBL server-side), placement runs, campaigns
  // for the run picker, and one per-mailbox DNS report each — allSettled so a
  // single slow or failing lookup degrades that one section, never the page.
  const [domainsRes, runsRes, campaignsRes, ...reports] = await Promise.allSettled([
    api.domainHealth(),
    api.placementRuns(),
    api.campaigns(),
    ...mailboxes.map((box) => api.deliverability(box.id)),
  ]);
  const domains: DomainHealth[] =
    domainsRes.status === "fulfilled" ? domainsRes.value : [];
  const runs: PlacementRun[] = runsRes.status === "fulfilled" ? runsRes.value : [];
  const campaigns: Campaign[] =
    campaignsRes.status === "fulfilled" ? campaignsRes.value : [];

  const ok: DeliverabilityReport[] = [];
  const failed: string[] = [];
  reports.forEach((r, i) => {
    if (r.status === "fulfilled") ok.push(r.value as DeliverabilityReport);
    else failed.push(mailboxes[i].email);
  });

  return (
    <>
      <PageHeader title="Deliverability" subtitle={SUBTITLE} />

      <div className="grid gap-5 animate-rise-delay-1">
        <div className="rounded-[var(--radius)] border border-line bg-surface p-5 shadow-[var(--shadow)]">
          <h2 className="font-[family-name:var(--font-display)] text-base text-ink">
            Send from a subdomain, never your primary domain
          </h2>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            Cold outbound carries reputation risk. Isolate it on a dedicated sending
            subdomain (e.g. <code className="font-mono">outbound.yourco.com</code>) with its
            own SPF/DKIM/DMARC, so a bad stretch never drags down the domain your real mail
            and website live on. Warm every new mailbox slowly — the ramp below is enforced
            automatically.
          </p>
        </div>

        {domainsRes.status === "rejected" ? (
          <p className="rounded-lg border border-warn/40 bg-warn-soft px-4 py-3 text-xs text-warn">
            Couldn’t load per-domain health — the DNS or blocklist lookup may have timed
            out. Reload to retry.
          </p>
        ) : domains.length > 0 ? (
          <section className="grid gap-5">
            <h2 className="font-[family-name:var(--font-display)] text-lg text-ink">
              Domain health
            </h2>
            {domains.map((d) => (
              <DomainHealthCard key={d.domain} health={d} />
            ))}
          </section>
        ) : null}

        <PlacementCard campaigns={campaigns} runs={runs} />

        {failed.length ? (
          <p className="rounded-lg border border-warn/40 bg-warn-soft px-4 py-3 text-xs text-warn">
            Couldn’t load a deliverability report for: {failed.join(", ")}. The DNS lookup
            may have timed out — reload to retry.
          </p>
        ) : null}

        {mailboxes.length === 0 ? (
          <EmptyState
            title="No mailboxes yet"
            body="Add a mailbox (POST /mailboxes, admin scope) — point SMTP at Mailpit for local sends. Its SPF/DKIM/DMARC status and warmup ramp show up here."
          />
        ) : (
          <section className="grid gap-5">
            <h2 className="font-[family-name:var(--font-display)] text-lg text-ink">
              Mailboxes
            </h2>
            {ok.map((report) => (
              <DeliverabilityCard key={report.mailbox_id} report={report} />
            ))}
          </section>
        )}
      </div>
    </>
  );
}
