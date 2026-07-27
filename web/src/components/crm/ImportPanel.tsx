"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { statsSummary } from "@/components/crm/RunsTable";
import type {
  Campaign,
  CRMConnection,
  CRMImportResult,
  CRMList,
  CRMPreviewAction,
} from "@/lib/types";

const inputCls =
  "w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-ink";
const labelCls =
  "block text-[11px] font-semibold uppercase tracking-[0.08em] text-faint";

const ACTION_TONES: Record<CRMPreviewAction, "good" | "info" | "muted" | "warn" | "accent"> = {
  create: "good",
  update: "info",
  unchanged: "muted",
  suppressed: "warn",
  no_email: "accent",
};

function changesText(changes: CRMImportResult["preview"][number]["changes"]): string {
  return Object.entries(changes)
    .map(([f, { from, to }]) => `${f}: ${from ?? "—"} → ${to ?? "—"}`)
    .join(" · ");
}

/** Import a CRM list: pick list + optional campaign, ALWAYS dry-run first,
 *  then commit the exact same selection. */
export function ImportPanel({
  connection,
  campaigns,
}: {
  connection: CRMConnection;
  campaigns: Campaign[];
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [lists, setLists] = useState<CRMList[] | null>(null);
  const [listsError, setListsError] = useState<string | null>(null);
  const [listId, setListId] = useState("");
  const [campaignId, setCampaignId] = useState("");
  const [preview, setPreview] = useState<CRMImportResult | null>(null);
  const [committed, setCommitted] = useState<CRMImportResult | null>(null);

  // The panel opening is the intent — fetch the connection's importable lists.
  useEffect(() => {
    let cancelled = false;
    api
      .crmLists(connection.id)
      .then((ls) => {
        if (cancelled) return;
        setLists(ls);
        setListId((current) => current || (ls[0]?.remote_id ?? ""));
      })
      .catch((e) => {
        if (!cancelled) setListsError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [connection.id]);

  /** Any change of selection voids the dry-run — commit must match a preview. */
  function select(setter: (v: string) => void) {
    return (e: React.ChangeEvent<HTMLSelectElement>) => {
      setter(e.target.value);
      setPreview(null);
      setCommitted(null);
      setError(null);
    };
  }

  function dryRun() {
    setError(null);
    setCommitted(null);
    startTransition(async () => {
      try {
        setPreview(
          await api.crmImport(connection.id, {
            list_id: listId,
            ...(campaignId ? { campaign_id: campaignId } : {}),
            dry_run: true,
          }),
        );
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  function commit() {
    setError(null);
    startTransition(async () => {
      try {
        const result = await api.crmImport(connection.id, {
          list_id: listId,
          ...(campaignId ? { campaign_id: campaignId } : {}),
          dry_run: false,
        });
        setCommitted(result);
        setPreview(null);
        router.refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  return (
    <div className="mt-4 rounded-lg border border-line bg-bg p-4">
      <p className="text-[11px] leading-relaxed text-muted">
        Contacts flow through the same ingest gate as a CSV import — dedupe,
        suppression, verification. The dry run shows exactly what a commit would do,
        including the lead fields the CRM would overwrite.
      </p>

      {listsError ? (
        <p className="mt-3 text-xs font-medium text-warn">
          Couldn’t load lists from {connection.provider}: {listsError}
        </p>
      ) : lists === null ? (
        <p className="mt-3 text-xs text-faint">Loading lists from {connection.provider}…</p>
      ) : lists.length === 0 ? (
        <p className="mt-3 text-xs text-faint">No importable lists in this CRM.</p>
      ) : (
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor={`import-list-${connection.id}`} className={labelCls}>
              List
            </label>
            <select
              id={`import-list-${connection.id}`}
              value={listId}
              onChange={select(setListId)}
              className={`mt-2 ${inputCls}`}
            >
              {lists.map((l) => (
                <option key={l.remote_id} value={l.remote_id}>
                  {l.name}
                  {l.size != null ? ` (${l.size})` : ""}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor={`import-campaign-${connection.id}`} className={labelCls}>
              Enroll into campaign (optional)
            </label>
            <select
              id={`import-campaign-${connection.id}`}
              value={campaignId}
              onChange={select(setCampaignId)}
              className={`mt-2 ${inputCls}`}
            >
              <option value="">— none —</option>
              {campaigns.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <div className="sm:col-span-2">
            <button
              type="button"
              disabled={pending || !listId}
              onClick={dryRun}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {pending && !preview ? "Previewing…" : "Preview import (dry run)"}
            </button>
          </div>
        </div>
      )}

      {error ? <p className="mt-3 text-xs font-medium text-red-600">{error}</p> : null}

      {preview ? (
        <div className="mt-4 border-t border-line pt-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-xs text-muted">
              <span className="font-semibold text-ink">Dry run</span> —{" "}
              {statsSummary(preview.stats)}. Nothing was written.
            </div>
            <button
              type="button"
              disabled={pending}
              onClick={commit}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {pending ? "Committing…" : "Commit import"}
            </button>
          </div>

          {preview.preview.length > 0 ? (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-line text-[11px] uppercase tracking-[0.08em] text-faint">
                  <tr>
                    <th className="py-2 pr-3 font-semibold">Email</th>
                    <th className="py-2 pr-3 font-semibold">Action</th>
                    <th className="py-2 font-semibold">Changes</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.preview.map((row, i) => (
                    <tr key={`${row.email}-${i}`} className="border-b border-line last:border-0">
                      <td className="py-2 pr-3">
                        <code className="font-mono text-xs text-ink">{row.email || "—"}</code>
                      </td>
                      <td className="py-2 pr-3">
                        <Badge tone={ACTION_TONES[row.action] ?? "muted"}>{row.action}</Badge>
                      </td>
                      <td className="py-2 text-xs text-muted">
                        {Object.keys(row.changes).length > 0 ? changesText(row.changes) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : null}

      {committed ? (
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-4 text-xs">
          <Badge tone="good">imported</Badge>
          <span className="text-muted">{statsSummary(committed.stats)}</span>
          {committed.run_id ? (
            <span className="text-faint">
              run <code className="font-mono">{committed.run_id}</code>
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
