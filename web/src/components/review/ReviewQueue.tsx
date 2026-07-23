"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { api } from "@/lib/api";
import type { ReviewAction, ReviewItem } from "@/lib/types";
import { Badge, statusTone } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";

const LABELS = [
  "interested",
  "objection",
  "not_now",
  "ooo",
  "unsubscribe",
  "bounce_or_auto",
] as const;

const btn =
  "rounded-lg border border-line bg-bg px-3 py-1.5 text-xs font-semibold text-muted transition-colors hover:border-ink hover:text-ink disabled:opacity-50";

function Context({ item }: { item: ReviewItem }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px] text-faint">
      <span className="text-sm font-semibold text-ink">
        {item.lead_name || item.lead_email || "Unknown lead"}
      </span>
      {item.lead_email && item.lead_name ? <span>{item.lead_email}</span> : null}
      {item.campaign_name ? <span>· {item.campaign_name}</span> : null}
      {item.enrollment_state ? (
        <Badge tone={statusTone(item.enrollment_state)}>{item.enrollment_state}</Badge>
      ) : null}
      {item.created_at ? <span>· {new Date(item.created_at).toLocaleString()}</span> : null}
    </div>
  );
}

/** Blocked copy: the validator rejected both attempts, so the enrollment is stuck in
 *  `error`. retry / skip / kill are the M0.6b recovery paths. */
function BlockedCopyCard({
  item,
  onAct,
  pending,
}: {
  item: ReviewItem;
  onAct: (item: ReviewItem, action: ReviewAction) => void;
  pending: boolean;
}) {
  const errors = (item.payload?.errors as string[] | undefined) ?? [];
  const slots = (item.payload?.slots as Record<string, string> | undefined) ?? {};

  return (
    <div className="rounded-[var(--radius)] border border-line bg-surface p-5 shadow-[var(--shadow)]">
      <Context item={item} />

      <div className="mt-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
          Why it was blocked
        </div>
        <ul className="mt-1 grid gap-1">
          {errors.length ? (
            errors.map((e) => (
              <li key={e} className="text-xs text-amber-600">
                {e}
              </li>
            ))
          ) : (
            <li className="text-xs text-muted">No validator detail recorded.</li>
          )}
        </ul>
      </div>

      {Object.keys(slots).length ? (
        <div className="mt-3">
          <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
            Rejected copy
          </div>
          <dl className="mt-1 grid gap-1 rounded-lg border border-dashed border-line px-3 py-2">
            {Object.entries(slots).map(([slot, value]) => (
              <div key={slot} className="grid gap-0.5">
                <dt className="font-mono text-[10px] text-faint">{slot}</dt>
                <dd className="text-xs leading-relaxed text-muted">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" disabled={pending} onClick={() => onAct(item, "retry")} className={btn}>
          Retry
          <span className="ml-1 font-normal text-faint">re-runs research and copy</span>
        </button>
        <button type="button" disabled={pending} onClick={() => onAct(item, "skip")} className={btn}>
          Skip step
          <span className="ml-1 font-normal text-faint">advances the sequence</span>
        </button>
        <button type="button" disabled={pending} onClick={() => onAct(item, "kill")} className={btn}>
          Kill
          <span className="ml-1 font-normal text-faint">leaves it stopped</span>
        </button>
      </div>
    </div>
  );
}

/** Low-confidence classification: the model was unsure, so the state change was held
 *  back. Approve applies the label at full confidence; override picks a different one. */
function ClassificationCard({
  item,
  onApprove,
  onOverride,
  pending,
}: {
  item: ReviewItem;
  onApprove: (item: ReviewItem) => void;
  onOverride: (item: ReviewItem, label: string) => void;
  pending: boolean;
}) {
  const label = item.payload?.label as string | undefined;
  const confidence = item.payload?.confidence as number | undefined;

  return (
    <div className="rounded-[var(--radius)] border border-line bg-surface p-5 shadow-[var(--shadow)]">
      <Context item={item} />

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
          Model said
        </span>
        {label ? <Badge tone={statusTone(label)}>{label.replaceAll("_", " ")}</Badge> : null}
        {confidence != null ? (
          <span className="text-xs text-muted">{Math.round(confidence * 100)}% confident</span>
        ) : null}
        <span className="text-[11px] text-faint">
          — below the threshold, so nothing downstream happened yet
        </span>
      </div>

      {item.message_subject || item.message_body ? (
        <div className="mt-3 rounded-lg border border-dashed border-line px-3 py-2">
          {item.message_subject ? (
            <div className="text-xs font-semibold text-ink">{item.message_subject}</div>
          ) : null}
          <pre className="mt-1 whitespace-pre-wrap font-sans text-xs leading-relaxed text-muted">
            {item.message_body || "(no body)"}
          </pre>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {item.message_id && label ? (
          <button
            type="button"
            disabled={pending}
            onClick={() => onApprove(item)}
            className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            Approve &ldquo;{label.replaceAll("_", " ")}&rdquo;
          </button>
        ) : null}
        <span className="text-[11px] text-faint">or override:</span>
        {LABELS.filter((l) => l !== label).map((l) => (
          <button
            key={l}
            type="button"
            disabled={pending || !item.message_id}
            onClick={() => onOverride(item, l)}
            className={btn}
          >
            {l.replaceAll("_", " ")}
          </button>
        ))}
        {!item.message_id ? (
          <span className="text-[11px] text-amber-600">
            No message linked — this item can only be dismissed.
          </span>
        ) : null}
      </div>
    </div>
  );
}

export function ReviewQueue({ initial }: { initial: ReviewItem[] }) {
  const router = useRouter();
  const [items, setItems] = useState(initial);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function drop(id: string) {
    setItems((prev) => prev.filter((i) => i.id !== id));
  }

  function run(fn: () => Promise<unknown>, id: string) {
    setError(null);
    startTransition(async () => {
      try {
        await fn();
        drop(id);
        router.refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  const act = (item: ReviewItem, action: ReviewAction) =>
    run(() => api.reviewAction(item.id, action), item.id);

  // Approve = apply the held-back label at full confidence, then clear the item.
  // `resolve` (not a re-drive) because reclassify already applied the state change.
  const approve = (item: ReviewItem) =>
    run(async () => {
      await api.reclassify(item.message_id!, item.payload!.label as string);
      await api.reviewAction(item.id, "resolve");
    }, item.id);

  const override = (item: ReviewItem, label: string) =>
    run(async () => {
      await api.reclassify(item.message_id!, label);
      await api.reviewAction(item.id, "resolve");
    }, item.id);

  if (!items.length) {
    return (
      <EmptyState
        title="Nothing to review"
        body="Blocked copy and low-confidence reply classifications land here. An empty queue means the validator and classifier are handling everything on their own."
      />
    );
  }

  const blocked = items.filter((i) => i.kind === "copywriter");
  const classifications = items.filter((i) => i.kind === "classification");

  return (
    <div className="grid gap-6">
      {error ? (
        <p className="rounded-lg border border-red-600/40 bg-surface px-4 py-3 text-xs text-red-600">
          {error}
        </p>
      ) : null}

      {blocked.length ? (
        <section>
          <h2 className="mb-3 font-[family-name:var(--font-display)] text-lg text-ink">
            Blocked copy ({blocked.length})
          </h2>
          <div className="grid gap-4">
            {blocked.map((item) => (
              <BlockedCopyCard key={item.id} item={item} onAct={act} pending={pending} />
            ))}
          </div>
        </section>
      ) : null}

      {classifications.length ? (
        <section>
          <h2 className="mb-3 font-[family-name:var(--font-display)] text-lg text-ink">
            Uncertain classifications ({classifications.length})
          </h2>
          <div className="grid gap-4">
            {classifications.map((item) => (
              <ClassificationCard
                key={item.id}
                item={item}
                onApprove={approve}
                onOverride={override}
                pending={pending}
              />
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
