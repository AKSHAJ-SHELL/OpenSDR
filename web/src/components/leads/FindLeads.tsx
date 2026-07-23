"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import { api } from "@/lib/api";
import { Badge, statusTone } from "@/components/ui/Badge";
import type { ImportResult, SourcedCandidate, SourcedPreview } from "@/lib/types";

const CSV_HINT = "comma-separated";

/** Provider-branded ICP search → gate-labeled preview → import selected. Honest by
 * construction: the preview labels come from the server gate, and import re-runs it. */
export function FindLeads({ providers }: { providers: string[] }) {
  const router = useRouter();
  const [provider, setProvider] = useState(providers[0]);
  const [icpQuery, setIcpQuery] = useState("");
  const [titles, setTitles] = useState("");
  const [seniorities, setSeniorities] = useState("");
  const [industries, setIndustries] = useState("");
  const [locations, setLocations] = useState("");
  const [employeeRanges, setEmployeeRanges] = useState("");
  const [limit, setLimit] = useState(25);

  const [preview, setPreview] = useState<SourcedPreview | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searching, startSearch] = useTransition();
  const [importing, startImport] = useTransition();

  const list = (s: string) =>
    s.split(",").map((x) => x.trim()).filter(Boolean);

  function search() {
    setError(null);
    setResult(null);
    setPreview(null);
    startSearch(async () => {
      try {
        const res = await api.sourceLeads({
          provider,
          icp_query: icpQuery,
          filters: {
            titles: list(titles),
            seniorities: list(seniorities),
            industries: list(industries),
            locations: list(locations),
            company_domains: [],
            employee_ranges: list(employeeRanges),
          },
          limit,
        });
        setPreview(res);
        // default-select every importable (new) candidate
        setSelected(
          new Set(res.candidates.filter((c) => c.status === "new").map((c) => c.email)),
        );
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  function toggle(email: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(email)) next.delete(email);
      else next.add(email);
      return next;
    });
  }

  const chosen = useMemo(
    () => (preview?.candidates ?? []).filter((c) => selected.has(c.email) && c.status === "new"),
    [preview, selected],
  );

  function importSelected() {
    if (!preview || chosen.length === 0) return;
    setError(null);
    startImport(async () => {
      try {
        const res = await api.importSourced(
          preview.provider,
          chosen.map((c) => ({
            email: c.email,
            first_name: c.first_name,
            last_name: c.last_name,
            title: c.title,
            company_name: c.company_name,
            company_domain: c.company_domain,
            linkedin_url: c.linkedin_url,
          })),
        );
        setResult(res);
        router.refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  return (
    <div className="grid gap-5 animate-rise-delay-1">
      {/* search form */}
      <div className="rounded-[var(--radius)] border border-line bg-surface p-5 shadow-[var(--shadow)]">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Provider">
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink"
            >
              {providers.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Result limit">
            <input
              type="number"
              min={1}
              max={50}
              value={limit}
              onChange={(e) => setLimit(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
              className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink"
            />
          </Field>
          <Field label="ICP description" full>
            <input
              value={icpQuery}
              onChange={(e) => setIcpQuery(e.target.value)}
              placeholder="e.g. warehouse operations leaders at mid-market logistics companies"
              className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink"
            />
          </Field>
          <Field label={`Titles (${CSV_HINT})`}>
            <input value={titles} onChange={(e) => setTitles(e.target.value)} placeholder="VP Operations, Head of Warehouse" className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink" />
          </Field>
          <Field label={`Seniorities (${CSV_HINT})`}>
            <input value={seniorities} onChange={(e) => setSeniorities(e.target.value)} placeholder="vp, director" className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink" />
          </Field>
          <Field label={`Industries (${CSV_HINT})`}>
            <input value={industries} onChange={(e) => setIndustries(e.target.value)} placeholder="logistics, manufacturing" className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink" />
          </Field>
          <Field label={`Locations (${CSV_HINT})`}>
            <input value={locations} onChange={(e) => setLocations(e.target.value)} placeholder="United States" className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink" />
          </Field>
          <Field label={`Employee ranges (${CSV_HINT})`} full>
            <input value={employeeRanges} onChange={(e) => setEmployeeRanges(e.target.value)} placeholder="51,200 · 201,500" className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink" />
          </Field>
        </div>
        <div className="mt-4 flex items-center justify-between">
          <p className="text-[11px] text-faint">
            Results come from your <span className="font-semibold text-muted">{provider}</span> account.
            No built-in database.
          </p>
          <button
            type="button"
            onClick={search}
            disabled={searching}
            className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {searching ? "Searching…" : "Search"}
          </button>
        </div>
      </div>

      {error ? <p className="text-xs font-medium text-red-600">{error}</p> : null}
      {result ? (
        <div className="rounded-lg border border-line bg-bg px-3 py-2 text-xs text-muted">
          Imported <span className="font-semibold text-ink">{result.imported}</span> · deduped{" "}
          <span className="font-semibold text-ink">{result.deduped}</span> · suppressed{" "}
          <span className="font-semibold text-ink">{result.suppressed}</span>. They now queue for
          verification and enrichment.
        </div>
      ) : null}

      {preview ? (
        <div className="rounded-[var(--radius)] border border-line bg-surface shadow-[var(--shadow)]">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-5 py-3 text-xs text-muted">
            <span>
              {preview.new} new · {preview.duplicate} duplicate · {preview.suppressed} suppressed ·{" "}
              {preview.invalid} no usable email
            </span>
            <button
              type="button"
              onClick={importSelected}
              disabled={importing || chosen.length === 0}
              className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {importing ? "Importing…" : `Import ${chosen.length} selected`}
            </button>
          </div>
          {preview.candidates.length === 0 ? (
            <p className="px-5 py-6 text-sm text-muted">No candidates returned for this search.</p>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="border-b border-line bg-bg/70 text-[11px] uppercase tracking-[0.08em] text-faint">
                <tr>
                  <th className="w-10 px-5 py-3" />
                  <th className="px-5 py-3 font-semibold">Lead</th>
                  <th className="px-5 py-3 font-semibold">Title</th>
                  <th className="px-5 py-3 font-semibold">Company</th>
                  <th className="px-5 py-3 font-semibold">Status</th>
                </tr>
              </thead>
              <tbody>
                {preview.candidates.map((c: SourcedCandidate, i) => {
                  const name = [c.first_name, c.last_name].filter(Boolean).join(" ");
                  const importable = c.status === "new";
                  return (
                    <tr key={`${c.email}-${i}`} className="border-b border-line last:border-0">
                      <td className="px-5 py-3">
                        <input
                          type="checkbox"
                          disabled={!importable}
                          checked={selected.has(c.email) && importable}
                          onChange={() => toggle(c.email)}
                        />
                      </td>
                      <td className="px-5 py-3">
                        <div className="font-semibold text-ink">{name || c.email}</div>
                        <div className="text-xs text-muted">{c.email}</div>
                      </td>
                      <td className="px-5 py-3 text-muted">{c.title || "—"}</td>
                      <td className="px-5 py-3 text-muted">{c.company_name || c.company_domain || "—"}</td>
                      <td className="px-5 py-3">
                        <Badge tone={importable ? statusTone("new") : "muted"}>
                          {c.status === "invalid" ? "no email" : c.status}
                        </Badge>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      ) : null}
    </div>
  );
}

function Field({
  label,
  children,
  full,
}: {
  label: string;
  children: React.ReactNode;
  full?: boolean;
}) {
  return (
    <label className={`grid gap-1 ${full ? "sm:col-span-2" : ""}`}>
      <span className="text-[11px] font-semibold uppercase tracking-wide text-faint">{label}</span>
      {children}
    </label>
  );
}
