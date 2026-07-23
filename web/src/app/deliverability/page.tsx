import { api } from "@/lib/api";
import { DeliverabilityCard } from "@/components/deliverability/DeliverabilityCard";
import { ApiDown } from "@/components/ui/ApiDown";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import type { DeliverabilityReport } from "@/lib/types";

export const dynamic = "force-dynamic";

const SUBTITLE =
  "Before you send: prove SPF, DKIM, and DMARC are set, and watch each mailbox warm up.";

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

  // One live DNS report per mailbox. allSettled so a single slow or failing lookup
  // degrades that one card, never the whole page.
  const reports = await Promise.allSettled(
    mailboxes.map((box) => api.deliverability(box.id)),
  );
  const ok: DeliverabilityReport[] = [];
  const failed: string[] = [];
  reports.forEach((r, i) => {
    if (r.status === "fulfilled") ok.push(r.value);
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
          ok.map((report) => <DeliverabilityCard key={report.mailbox_id} report={report} />)
        )}
      </div>
    </>
  );
}
