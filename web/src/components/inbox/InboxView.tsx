"use client";

import { useMemo, useState, useTransition } from "react";
import { api } from "@/lib/api";
import type { InboxMessage } from "@/lib/types";
import { Badge, statusTone } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";

const LABELS = [
  "all",
  "interested",
  "objection",
  "not_now",
  "ooo",
  "unsubscribe",
  "bounce_or_auto",
] as const;

export function InboxView({ initial }: { initial: InboxMessage[] }) {
  const [messages, setMessages] = useState(initial);
  const [filter, setFilter] = useState<(typeof LABELS)[number]>("all");
  const [selectedId, setSelectedId] = useState<string | null>(
    initial[0]?.id ?? null,
  );
  const [pending, startTransition] = useTransition();

  const filtered = useMemo(() => {
    if (filter === "all") return messages;
    return messages.filter((m) => m.classification === filter);
  }, [messages, filter]);

  const selected =
    filtered.find((m) => m.id === selectedId) ?? filtered[0] ?? null;

  function load(label: (typeof LABELS)[number]) {
    setFilter(label);
    startTransition(async () => {
      const data = await api.inbox(label === "all" ? undefined : label);
      setMessages(data);
      setSelectedId(data[0]?.id ?? null);
    });
  }

  function reclassify(label: string) {
    if (!selected) return;
    startTransition(async () => {
      const updated = await api.reclassify(selected.id, label);
      setMessages((prev) =>
        prev.map((m) => (m.id === updated.id ? updated : m)),
      );
    });
  }

  if (!messages.length && filter === "all") {
    return (
      <EmptyState
        title="Inbox is quiet"
        body="When prospects reply, threads land here with classification and a one-click human override."
      />
    );
  }

  return (
    <div className="animate-rise-delay-1 grid min-h-[560px] overflow-hidden rounded-[var(--radius)] border border-line bg-surface shadow-[var(--shadow)] lg:grid-cols-[320px_1fr]">
      <div className="border-b border-line lg:border-b-0 lg:border-r">
        <div className="flex flex-wrap gap-1.5 border-b border-line p-3">
          {LABELS.map((label) => (
            <button
              key={label}
              type="button"
              onClick={() => load(label)}
              className={`rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide transition-colors ${
                filter === label
                  ? "bg-ink text-white"
                  : "bg-bg text-muted hover:text-ink"
              }`}
            >
              {label.replaceAll("_", " ")}
            </button>
          ))}
        </div>
        <div className={`max-h-[520px] overflow-auto ${pending ? "opacity-60" : ""}`}>
          {filtered.length === 0 ? (
            <p className="p-6 text-sm text-muted">No messages in this filter.</p>
          ) : (
            filtered.map((msg) => {
              const active = selected?.id === msg.id;
              return (
                <button
                  key={msg.id}
                  type="button"
                  onClick={() => setSelectedId(msg.id)}
                  className={`block w-full border-b border-line px-4 py-3.5 text-left transition-colors ${
                    active ? "bg-accent-soft" : "hover:bg-bg"
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-ink">
                        {msg.lead_name || msg.lead_email || "Unknown"}
                      </div>
                      <div className="mt-0.5 truncate text-xs text-muted">
                        {msg.company_domain || msg.lead_email}
                      </div>
                    </div>
                    {msg.classification ? (
                      <Badge tone={statusTone(msg.classification)}>
                        {msg.classification.replaceAll("_", " ")}
                      </Badge>
                    ) : null}
                  </div>
                  <div className="mt-2 truncate text-sm text-ink/90">
                    {msg.subject || "(no subject)"}
                  </div>
                </button>
              );
            })
          )}
        </div>
      </div>

      <div className="flex min-h-[420px] flex-col">
        {selected ? (
          <>
            <div className="border-b border-line px-6 py-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="font-[family-name:var(--font-display)] text-2xl text-ink">
                    {selected.lead_name || selected.lead_email}
                  </h2>
                  <p className="mt-1 text-sm text-muted">
                    {selected.lead_email}
                    {selected.company_domain
                      ? ` · ${selected.company_domain}`
                      : ""}
                  </p>
                </div>
                {selected.classification ? (
                  <Badge tone={statusTone(selected.classification)}>
                    {selected.classification.replaceAll("_", " ")}
                    {selected.classification_confidence != null
                      ? ` · ${Math.round(selected.classification_confidence * 100)}%`
                      : ""}
                  </Badge>
                ) : null}
              </div>
              <h3 className="mt-4 text-base font-semibold text-ink">
                {selected.subject || "(no subject)"}
              </h3>
            </div>
            <div className="flex-1 overflow-auto px-6 py-5">
              <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-ink/90">
                {selected.body || "No body."}
              </pre>
            </div>
            <div className="border-t border-line px-6 py-4">
              <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">
                Override classification
              </div>
              <div className="flex flex-wrap gap-2">
                {LABELS.filter((l) => l !== "all").map((label) => (
                  <button
                    key={label}
                    type="button"
                    disabled={pending}
                    onClick={() => reclassify(label)}
                    className="rounded-lg border border-line bg-bg px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:border-ink hover:text-ink disabled:opacity-50"
                  >
                    {label.replaceAll("_", " ")}
                  </button>
                ))}
              </div>
            </div>
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center p-8 text-sm text-muted">
            Select a thread
          </div>
        )}
      </div>
    </div>
  );
}
