import type { DnsStatus, DomainHealth } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";

const DNS_LABEL: Record<DnsStatus, string> = {
  pass: "pass",
  missing: "missing",
  error: "couldn’t check",
};

const COMPONENT_LABEL: Record<string, string> = {
  dns_auth: "DNS auth",
  blocklist: "Blocklists",
  bounce_rate: "Bounce rate",
  complaint_rate: "Complaints",
};

function dnsTone(status: DnsStatus): "good" | "warn" | "muted" {
  if (status === "pass") return "good";
  if (status === "missing") return "warn";
  return "muted"; // couldn't check — never a false red or green
}

function scoreTone(score: number): string {
  if (score >= 80) return "text-good";
  if (score >= 50) return "text-warn";
  return "text-red-600";
}

function pct(rate: number): string {
  return `${(rate * 100).toFixed(rate > 0 && rate < 0.001 ? 3 : 1)}%`;
}

/** One sending domain: score, deduction breakdown, DNS auth, blocklists, 7-day stats.
 *  The score formula lives (documented) in craftsman/deliverability/health.py. */
export function DomainHealthCard({ health }: { health: DomainHealth }) {
  const { stats_7d: stats } = health;
  const deductions = Object.entries(health.components).filter(([, v]) => v > 0);
  return (
    <div className="rounded-[var(--radius)] border border-line bg-surface p-6 shadow-[var(--shadow)]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="font-semibold text-ink">
            <code className="font-mono">{health.domain}</code>
          </div>
          <div className="mt-0.5 text-xs text-muted">
            {health.mailboxes} mailbox{health.mailboxes === 1 ? "" : "es"}
            {health.paused_mailboxes > 0 ? (
              <span className="text-warn"> · {health.paused_mailboxes} paused</span>
            ) : null}
          </div>
        </div>
        <div className="text-right">
          <div className={`font-[family-name:var(--font-display)] text-3xl ${scoreTone(health.score)}`}>
            {health.score}
          </div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-faint">
            health / 100
          </div>
        </div>
      </div>

      {health.paused_mailboxes >= health.mailboxes ? (
        <div className="mt-3 rounded-lg border border-warn/40 bg-warn-soft px-4 py-3">
          <p className="text-xs leading-relaxed text-warn">
            <span className="font-semibold">Every mailbox on this domain is paused</span>{" "}
            (auto-pause fires when a domain crosses its daily bounce budget). Fix the
            cause, then un-pause each mailbox from its settings.
          </p>
        </div>
      ) : null}

      {deductions.length > 0 ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {deductions.map(([key, points]) => (
            <span
              key={key}
              className="rounded-full bg-warn-soft px-2.5 py-0.5 text-[11px] font-semibold text-warn"
            >
              {COMPONENT_LABEL[key] ?? key} −{points}
            </span>
          ))}
        </div>
      ) : (
        <p className="mt-4 text-xs text-good">No deductions — clean bill of health.</p>
      )}

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div className="rounded-lg border border-line bg-bg p-4">
          <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
            DNS auth
          </div>
          <div className="mt-2 grid gap-1.5">
            {(
              [
                ["SPF", health.spf.status],
                ["DKIM", health.dkim.status],
                ["DMARC", health.dmarc.status],
              ] as const
            ).map(([name, status]) => (
              <div key={name} className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-ink">{name}</span>
                <Badge tone={dnsTone(status)}>{DNS_LABEL[status]}</Badge>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-lg border border-line bg-bg p-4">
          <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
            Blocklists
          </div>
          <div className="mt-2 grid gap-1.5">
            {health.blocklists.map((b) => (
              <div key={b.zone} className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate font-mono text-xs text-ink">{b.zone}</span>
                <Badge tone={b.status === "clear" ? "good" : b.status === "listed" ? "warn" : "muted"}>
                  {b.status === "error" ? "couldn’t check" : b.status}
                </Badge>
              </div>
            ))}
            {health.blocklists.some((b) => b.status === "listed") ? (
              <p className="mt-1 text-[11px] text-warn">
                Listed IPs:{" "}
                {health.blocklists
                  .flatMap((b) => b.listed_ips)
                  .filter((ip, i, all) => all.indexOf(ip) === i)
                  .join(", ")}{" "}
                — request delisting with the blocklist operator.
              </p>
            ) : null}
          </div>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted">
        <span>
          last 7 days: <span className="font-semibold text-ink">{stats.sends}</span> sent
        </span>
        <span>
          <span className="font-semibold text-ink">{stats.hard_bounces}</span> hard bounces
          {stats.sends > 0 ? ` (${pct(stats.bounce_rate)})` : ""}
        </span>
        <span>
          <span className="font-semibold text-ink">{stats.spam_bounces}</span> spam-flagged
          bounces{stats.sends > 0 ? ` (${pct(stats.complaint_rate)})` : ""}
        </span>
      </div>
    </div>
  );
}
