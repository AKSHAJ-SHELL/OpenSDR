import type { DeliverabilityReport, DnsStatus } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { CopyButton } from "./CopyButton";

const STATUS_LABEL: Record<DnsStatus, string> = {
  pass: "pass",
  missing: "missing",
  error: "couldn’t check",
};

function statusTone(status: DnsStatus): "good" | "warn" | "muted" {
  if (status === "pass") return "good";
  if (status === "missing") return "warn";
  return "muted"; // error / couldn't check — never a false red or green
}

function RecordBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="mt-2">
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-faint">
        {label}
      </div>
      <div className="flex items-start gap-2 rounded-lg border border-dashed border-line bg-bg px-3 py-2">
        <code className="min-w-0 flex-1 break-all font-mono text-[11px] leading-relaxed text-muted">
          {value}
        </code>
        <CopyButton value={value} />
      </div>
    </div>
  );
}

function DnsRow({
  name,
  status,
  found,
  children,
}: {
  name: string;
  status: DnsStatus;
  found: string | null;
  children?: React.ReactNode;
}) {
  return (
    <div className="border-t border-line py-4 first:border-t-0 first:pt-0">
      <div className="flex items-center gap-2">
        <span className="font-semibold text-ink">{name}</span>
        <Badge tone={statusTone(status)}>{STATUS_LABEL[status]}</Badge>
      </div>
      {found ? <RecordBlock label="Found" value={found} /> : null}
      {children}
    </div>
  );
}

/** Ramp bar: five stages, current one highlighted. The ramp advances one stage per day. */
function WarmupRamp({ warmup }: { warmup: DeliverabilityReport["warmup"] }) {
  return (
    <div className="mt-5 rounded-lg border border-line bg-bg p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
          Warmup ramp
        </span>
        <span className="text-xs text-muted">
          {warmup.sent_today}/{warmup.effective_cap} sent today · one stage per day
        </span>
      </div>
      <div className="mt-3 flex gap-1.5">
        {warmup.schedule.map((step) => {
          const current = step.stage === warmup.stage;
          return (
            <div key={step.stage} className="flex-1 text-center">
              <div
                className={`h-1.5 rounded-full ${current ? "bg-accent" : "bg-line"}`}
                title={`day ${step.day}`}
              />
              <div
                className={`mt-1 text-[11px] ${current ? "font-semibold text-ink" : "text-faint"}`}
              >
                {step.cap}
              </div>
            </div>
          );
        })}
      </div>
      <p className="mt-2 text-[11px] text-faint">
        {warmup.stage >= 4
          ? "Fully warmed — sending at the full daily limit."
          : `Currently capped at ${warmup.effective_cap}/day; reaches the full ${warmup.daily_limit} at stage 4.`}
      </p>
    </div>
  );
}

export function DeliverabilityCard({ report }: { report: DeliverabilityReport }) {
  const { spf, dmarc, dkim, warmup } = report;
  return (
    <div className="rounded-[var(--radius)] border border-line bg-surface p-6 shadow-[var(--shadow)]">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <div className="font-semibold text-ink">{report.email}</div>
          <div className="text-xs text-muted">
            sending domain <code className="font-mono text-ink">{report.domain}</code>
          </div>
        </div>
      </div>

      {report.primary_domain_warning ? (
        <div className="mt-3 rounded-lg border border-warn/40 bg-warn-soft px-4 py-3">
          <p className="text-xs leading-relaxed text-warn">
            <span className="font-semibold">This looks like a primary domain.</span> Send
            from a dedicated subdomain (e.g. <code className="font-mono">outbound.{report.domain}</code>)
            instead — a deliverability problem there never poisons the reputation of the
            domain your real email and website depend on.
          </p>
        </div>
      ) : null}

      <div className="mt-5">
        <DnsRow name="SPF" status={spf.status} found={spf.record}>
          {spf.status !== "pass" && spf.recommended ? (
            <>
              <RecordBlock label="Recommended (set your provider)" value={spf.recommended} />
              <p className="mt-1 text-[11px] text-faint">
                Replace <code className="font-mono">YOUR-PROVIDER</code> with your email
                provider’s SPF include (e.g. <code className="font-mono">_spf.google.com</code>).
              </p>
            </>
          ) : null}
        </DnsRow>

        <DnsRow name="DKIM" status={dkim.status} found={dkim.record}>
          {dkim.selector ? (
            <p className="mt-2 text-[11px] text-faint">
              Selector checked: <code className="font-mono text-muted">{dkim.selector}</code>
            </p>
          ) : (
            <p className="mt-2 text-[11px] text-faint">
              No selector set — probed the common ones. If yours is custom, set it on the
              mailbox so this check is exact.
            </p>
          )}
          {dkim.status !== "pass" ? (
            <p className="mt-1 text-[11px] text-faint">
              DKIM keys come from your sending provider — enable DKIM there, then publish
              the record it gives you. We verify it, we can’t generate it.
            </p>
          ) : null}
        </DnsRow>

        <DnsRow name="DMARC" status={dmarc.status} found={dmarc.record}>
          {dmarc.status === "pass" && dmarc.policy ? (
            <p className="mt-2 text-[11px] text-faint">
              Policy: <code className="font-mono text-muted">p={dmarc.policy}</code>
            </p>
          ) : null}
          {dmarc.status !== "pass" && dmarc.recommended ? (
            <RecordBlock label="Recommended starter (p=none)" value={dmarc.recommended} />
          ) : null}
        </DnsRow>
      </div>

      <WarmupRamp warmup={warmup} />
    </div>
  );
}
