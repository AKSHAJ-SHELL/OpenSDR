"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { FieldMapEditor } from "@/components/crm/FieldMapEditor";
import { ImportPanel } from "@/components/crm/ImportPanel";
import { RunsTable, statsSummary } from "@/components/crm/RunsTable";
import type { Campaign, CRMConnection, CRMSyncRun, CRMTestResult } from "@/lib/types";

const btn =
  "rounded-lg border border-line bg-bg px-2.5 py-1 text-[11px] font-semibold text-muted transition-colors hover:border-ink hover:text-ink disabled:opacity-50";
const inputCls =
  "w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-ink";
const labelCls =
  "block text-[11px] font-semibold uppercase tracking-[0.08em] text-faint";

const CRED_FIELDS: Record<string, { key: string; label: string; type: string }[]> = {
  hubspot: [{ key: "access_token", label: "Private app access token", type: "password" }],
  salesforce: [
    { key: "instance_url", label: "Instance URL", type: "url" },
    { key: "client_id", label: "Client ID", type: "text" },
    { key: "client_secret", label: "Client secret", type: "password" },
  ],
};

function fmtDate(iso: string | null): string {
  return iso ? iso.slice(0, 16).replace("T", " ") : "—";
}

type Section = "mapping" | "import" | "runs" | null;

/** One CRM connection: status row, test/sync/credential actions, and the
 *  mapping / import / runs panels behind toggles. */
export function ConnectionCard({
  connection,
  runs,
  campaigns,
  canAdmin,
  canOperate,
}: {
  connection: CRMConnection;
  runs: CRMSyncRun[];
  campaigns: Campaign[];
  canAdmin: boolean;
  canOperate: boolean;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [section, setSection] = useState<Section>(null);
  const [testResult, setTestResult] = useState<CRMTestResult | null>(null);
  const [syncRun, setSyncRun] = useState<CRMSyncRun | null>(null);
  const [rotating, setRotating] = useState(false);
  const [creds, setCreds] = useState<Record<string, string>>({});

  const credFields = CRED_FIELDS[connection.provider] ?? [];
  const credsMissing = credFields.some((f) => !creds[f.key]?.trim());

  function run(fn: () => Promise<void>) {
    setError(null);
    startTransition(async () => {
      try {
        await fn();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  function test() {
    setTestResult(null);
    run(async () => {
      setTestResult(await api.testCrmConnection(connection.id));
    });
  }

  function syncNow() {
    setSyncRun(null);
    run(async () => {
      setSyncRun(await api.crmSync(connection.id));
      router.refresh();
    });
  }

  function toggleActive() {
    run(async () => {
      await api.updateCrmConnection(connection.id, { active: !connection.active });
      router.refresh();
    });
  }

  function rotateCredentials() {
    run(async () => {
      const credentials = Object.fromEntries(
        credFields.map((f) => [f.key, creds[f.key].trim()]),
      );
      await api.updateCrmConnection(connection.id, { credentials });
      // write-only: nothing to render back — clear and close
      setCreds({});
      setRotating(false);
      router.refresh();
    });
  }

  function sectionBtn(key: Exclude<Section, null>, label: string) {
    const open = section === key;
    return (
      <button
        type="button"
        onClick={() => setSection(open ? null : key)}
        className={`rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors ${
          open
            ? "border-ink bg-bg text-ink"
            : "border-line bg-bg text-muted hover:border-ink hover:text-ink"
        }`}
      >
        {label}
      </button>
    );
  }

  return (
    <div className="rounded-[var(--radius)] border border-line bg-surface p-6 shadow-[var(--shadow)]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-[family-name:var(--font-display)] text-base text-ink">
              {connection.name}
            </span>
            <Badge tone="info">{connection.provider}</Badge>
            <Badge tone={connection.active ? "good" : "muted"}>
              {connection.active ? "active" : "inactive"}
            </Badge>
          </div>
          <p className="mt-1 text-xs text-muted">
            Last outbound watermark:{" "}
            <span className="font-mono">{fmtDate(connection.outbound_watermark)}</span>
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {canAdmin ? (
            <>
              <button type="button" disabled={pending} onClick={test} className={btn}>
                Test
              </button>
              <button type="button" disabled={pending} onClick={toggleActive} className={btn}>
                {connection.active ? "Deactivate" : "Activate"}
              </button>
              <button
                type="button"
                disabled={pending}
                onClick={() => {
                  setRotating(!rotating);
                  setCreds({});
                  setError(null);
                }}
                className={btn}
              >
                Rotate credentials
              </button>
            </>
          ) : null}
          {canOperate ? (
            <button type="button" disabled={pending} onClick={syncNow} className={btn}>
              {pending ? "Working…" : "Sync now"}
            </button>
          ) : null}
        </div>
      </div>

      {testResult ? (
        <p className="mt-3 flex items-center gap-2 text-xs">
          <Badge tone={testResult.ok ? "good" : "warn"}>
            {testResult.ok ? "ok" : "failed"}
          </Badge>
          <span className={testResult.ok ? "text-muted" : "text-warn"}>
            {testResult.detail}
          </span>
        </p>
      ) : null}

      {syncRun ? (
        <p className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <Badge tone={syncRun.status === "succeeded" ? "good" : "warn"}>
            sync {syncRun.status}
          </Badge>
          <span className="text-muted">{statsSummary(syncRun.stats)}</span>
          {syncRun.error ? <span className="text-warn">{syncRun.error}</span> : null}
        </p>
      ) : null}

      {rotating ? (
        <div className="mt-4 rounded-lg border border-line bg-bg p-4">
          <p className="text-[11px] leading-relaxed text-muted">
            Replace every credential for this connection. The current ones are never
            shown; the new ones are encrypted on save and never shown either.
          </p>
          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            {credFields.map((f) => (
              <div key={f.key}>
                <label htmlFor={`rotate-${connection.id}-${f.key}`} className={labelCls}>
                  {f.label}
                </label>
                <input
                  id={`rotate-${connection.id}-${f.key}`}
                  type={f.type}
                  autoComplete="off"
                  value={creds[f.key] ?? ""}
                  onChange={(e) => setCreds({ ...creds, [f.key]: e.target.value })}
                  className={`mt-2 ${inputCls}`}
                />
              </div>
            ))}
          </div>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              disabled={pending || credsMissing}
              onClick={rotateCredentials}
              className="rounded-lg bg-accent px-2.5 py-1 text-[11px] font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {pending ? "Saving…" : "Save credentials"}
            </button>
            <button
              type="button"
              onClick={() => {
                setRotating(false);
                setCreds({});
              }}
              className={btn}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {error ? <p className="mt-3 text-xs font-medium text-red-600">{error}</p> : null}

      <div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-4">
        {sectionBtn("mapping", "Field mapping")}
        {canOperate ? sectionBtn("import", "Import from list") : null}
        {sectionBtn("runs", `Runs (${runs.length})`)}
      </div>

      {section === "mapping" ? (
        <FieldMapEditor connection={connection} canAdmin={canAdmin} />
      ) : null}
      {section === "import" && canOperate ? (
        <ImportPanel connection={connection} campaigns={campaigns} />
      ) : null}
      {section === "runs" ? <RunsTable runs={runs} /> : null}
    </div>
  );
}
