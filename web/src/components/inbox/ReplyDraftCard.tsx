"use client";

import { useState, useTransition } from "react";
import { api } from "@/lib/api";
import type { ReplyDraft } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";

const RESOLVED_LABEL: Record<string, string> = {
  sent: "Sent",
  edited_sent: "Sent (edited)",
  discarded: "Discarded",
  failed: "Validator rejected — in review queue",
  skipped: "No draft — needs a human reply",
};

/**
 * Copilot draft (M4.1): the validated reply waiting under the thread. A human
 * click here is the only path to dispatch — approve as-is, edit (the edit is
 * re-validated server-side by the same gates the LLM faced), or discard.
 */
export function ReplyDraftCard({
  draft,
  onChange,
}: {
  draft: ReplyDraft;
  onChange: (updated: ReplyDraft) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [body, setBody] = useState(draft.body ?? "");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const edited = body !== (draft.body ?? "");

  function send() {
    setError(null);
    startTransition(async () => {
      try {
        const updated = await api.sendDraft(draft.id, edited ? body : undefined);
        onChange(updated);
        setEditing(false);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  function discard() {
    setError(null);
    startTransition(async () => {
      try {
        onChange(await api.discardDraft(draft.id));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  if (draft.status !== "pending") {
    const note = RESOLVED_LABEL[draft.status];
    if (!note) return null;
    return (
      <div className="mt-4 rounded-lg border border-line bg-bg px-4 py-3">
        <div className="flex items-center gap-2">
          <Badge tone={draft.status.endsWith("sent") ? "good" : "muted"}>
            Copilot draft
          </Badge>
          <span className="text-xs text-muted">
            {note}
            {draft.auto_sent ? " · Autopilot" : ""}
          </span>
        </div>
        {draft.status.endsWith("sent") && draft.body ? (
          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap font-sans text-xs leading-relaxed text-muted">
            {draft.body}
          </pre>
        ) : null}
      </div>
    );
  }

  return (
    <div className="mt-4 rounded-lg border border-accent/40 bg-accent-soft/40 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge tone="accent">Copilot draft</Badge>
          <span className="text-[11px] uppercase tracking-wide text-faint">
            {draft.skeleton_key?.replaceAll("_", " ")}
          </span>
        </div>
        <span className="text-[11px] text-faint">
          Validated · a human click is the only path to dispatch
        </span>
      </div>

      {editing ? (
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={Math.min(14, Math.max(6, body.split("\n").length + 1))}
          className="mt-3 w-full rounded-lg border border-line bg-surface p-3 font-sans text-sm leading-relaxed text-ink focus:border-ink focus:outline-none"
        />
      ) : (
        <pre className="mt-3 whitespace-pre-wrap font-sans text-sm leading-relaxed text-ink/90">
          {body}
        </pre>
      )}

      {error ? (
        <p className="mt-2 whitespace-pre-wrap text-xs text-warn">
          {error.includes("validation_errors")
            ? `The edit failed validation — Craftsman never sends what its validator rejects. ${error}`
            : error}
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={pending}
          onClick={send}
          className="rounded-lg bg-ink px-3.5 py-1.5 text-xs font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {edited ? "Send edited reply" : "Approve & send"}
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() => setEditing((v) => !v)}
          className="rounded-lg border border-line bg-surface px-3.5 py-1.5 text-xs font-medium text-muted transition-colors hover:border-ink hover:text-ink disabled:opacity-50"
        >
          {editing ? "Preview" : "Edit"}
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={discard}
          className="rounded-lg border border-line bg-surface px-3.5 py-1.5 text-xs font-medium text-muted transition-colors hover:border-warn hover:text-warn disabled:opacity-50"
        >
          Discard
        </button>
      </div>
    </div>
  );
}
