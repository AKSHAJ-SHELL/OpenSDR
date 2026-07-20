import { api } from "@/lib/api";
import { ApiDown } from "@/components/ui/ApiDown";
import { Badge, statusTone } from "@/components/ui/Badge";
import { Metric } from "@/components/ui/Metric";
import { PageHeader } from "@/components/ui/PageHeader";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  let overview;
  let mailboxes;
  let inbox;
  let review;

  try {
    [overview, mailboxes, inbox, review] = await Promise.all([
      api.overview(),
      api.mailboxes(),
      api.inbox("interested"),
      api.review(),
    ]);
  } catch (e) {
    return (
      <>
        <PageHeader
          title="Overview"
          subtitle="Your agent finds fit leads, sends constrained copy, and learns from replies."
        />
        <ApiDown error={e instanceof Error ? e.message : String(e)} />
      </>
    );
  }

  const states = Object.entries(overview.enrollment_states).sort(
    (a, b) => b[1] - a[1],
  );
  const attention = [
    ...inbox.slice(0, 5).map((m) => ({
      kind: "interested" as const,
      title: m.lead_name || m.lead_email || "Lead",
      detail: m.subject || "Interested reply",
    })),
    ...review.slice(0, 5).map((r) => ({
      kind: "review" as const,
      title: r.kind,
      detail: r.created_at
        ? new Date(r.created_at).toLocaleString()
        : "Needs human review",
    })),
  ].slice(0, 8);

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="Your agent finds fit leads, sends constrained copy, and learns from replies."
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4 animate-rise-delay-1">
        <Metric label="Sent" value={overview.sent} />
        <Metric
          label="Reply rate"
          value={`${(overview.reply_rate * 100).toFixed(1)}%`}
          hint={`${overview.replies} human replies`}
        />
        <Metric
          label="Interested"
          value={overview.interested}
          hint="Ready for human handoff"
        />
        <Metric
          label="Copy blocked"
          value={overview.copywriter_rejections}
          hint="Validator rejected hallucinated slots"
        />
      </div>

      <div className="mt-8 grid gap-6 lg:grid-cols-[1.2fr_0.8fr] animate-rise-delay-2">
        <section className="rounded-[var(--radius)] border border-line bg-surface p-6 shadow-[var(--shadow)]">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-[family-name:var(--font-display)] text-xl text-ink">
              Needs attention
            </h2>
            <Badge tone="accent">{attention.length} open</Badge>
          </div>
          {attention.length === 0 ? (
            <p className="text-sm text-muted">
              Nothing waiting. Interested replies and review-queue items show up
              here.
            </p>
          ) : (
            <ul className="divide-y divide-line">
              {attention.map((item, i) => (
                <li
                  key={`${item.kind}-${item.title}-${i}`}
                  className="flex items-center justify-between gap-3 py-3"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-ink">
                      {item.title}
                    </div>
                    <div className="truncate text-xs text-muted">{item.detail}</div>
                  </div>
                  <Badge tone={item.kind === "interested" ? "good" : "warn"}>
                    {item.kind}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-[var(--radius)] border border-line bg-surface p-6 shadow-[var(--shadow)]">
          <h2 className="mb-4 font-[family-name:var(--font-display)] text-xl text-ink">
            Pipeline
          </h2>
          {states.length === 0 ? (
            <p className="text-sm text-muted">No enrollments yet.</p>
          ) : (
            <ul className="space-y-3">
              {states.map(([state, n]) => {
                const max = states[0]?.[1] || 1;
                const pct = Math.max(8, Math.round((n / max) * 100));
                return (
                  <li key={state}>
                    <div className="mb-1 flex justify-between text-xs">
                      <span className="font-medium text-ink">
                        {state.replaceAll("_", " ")}
                      </span>
                      <span className="text-muted">{n}</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-bg">
                      <div
                        className="h-full rounded-full bg-accent transition-all duration-500"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          )}

          <h3 className="mb-3 mt-8 text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
            Mailboxes
          </h3>
          {mailboxes.length === 0 ? (
            <p className="text-sm text-muted">
              Add a mailbox via API — point SMTP at Mailpit for local sends.
            </p>
          ) : (
            <ul className="space-y-2">
              {mailboxes.map((box) => (
                <li
                  key={box.id}
                  className="flex items-center justify-between rounded-lg bg-bg px-3 py-2.5"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-ink">
                      {box.email}
                    </div>
                    <div className="text-xs text-muted">
                      {box.sent_today}/{box.daily_limit} today · warmup{" "}
                      {box.warmup_stage}
                    </div>
                  </div>
                  <Badge tone={statusTone(box.health)}>{box.health}</Badge>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </>
  );
}
