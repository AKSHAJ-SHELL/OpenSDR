"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { api } from "@/lib/api";
import type { Lead } from "@/lib/types";

const btn =
  "rounded-lg border border-line bg-bg px-2.5 py-1 text-[11px] font-semibold text-muted transition-colors hover:border-ink hover:text-ink disabled:opacity-50";

/**
 * Per-lead operations. Suppress is reversible-ish and needs `operate`; erase is a
 * hard GDPR delete and needs `admin`, which the dashboard key deliberately lacks by
 * default — a 403 is explained rather than swallowed.
 */
export function LeadActions({ lead }: { lead: Lead }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [confirmErase, setConfirmErase] = useState(false);
  const [typed, setTyped] = useState("");

  function suppress() {
    if (!window.confirm(`Stop all mail to ${lead.email}?\n\nThe lead row is kept; only sending stops.`)) {
      return;
    }
    setError(null);
    startTransition(async () => {
      try {
        await api.suppressLead(lead.id);
        router.refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  function erase() {
    setError(null);
    startTransition(async () => {
      try {
        await api.eraseLead(lead.id);
        setConfirmErase(false);
        router.refresh();
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        setError(
          message.startsWith("403")
            ? "Your dashboard key lacks the admin scope. That is the default: erasure is irreversible, so it is kept off the dashboard key. Erase via an admin key, or mint the dashboard an admin key if you accept the wider blast radius."
            : message,
        );
      }
    });
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex gap-1.5">
        {lead.status !== "suppressed" ? (
          <button type="button" disabled={pending} onClick={suppress} className={btn}>
            Suppress
          </button>
        ) : null}
        <button
          type="button"
          disabled={pending}
          onClick={() => setConfirmErase(!confirmErase)}
          className={btn}
        >
          Erase
        </button>
      </div>

      {confirmErase ? (
        <div className="mt-1 w-72 rounded-lg border border-red-600/40 bg-surface p-3 text-left">
          <p className="text-[11px] leading-relaxed text-muted">
            Permanently deletes this lead and every message, enrollment, and dry-run item
            naming them. The suppression record survives on purpose. This cannot be undone
            — type <span className="font-mono text-ink">erase</span> to confirm.
          </p>
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            className="mt-2 w-full rounded border border-line bg-bg px-2 py-1 text-xs text-ink outline-none focus:border-ink"
          />
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              disabled={pending || typed !== "erase"}
              onClick={erase}
              className="rounded-lg bg-red-600 px-2.5 py-1 text-[11px] font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {pending ? "Erasing…" : "Erase permanently"}
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirmErase(false);
                setTyped("");
              }}
              className={btn}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {error ? (
        <p className="max-w-xs text-right text-[11px] leading-relaxed text-red-600">{error}</p>
      ) : null}
    </div>
  );
}
