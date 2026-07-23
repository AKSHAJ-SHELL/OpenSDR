"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, useTransition } from "react";
import { api } from "@/lib/api";
import type { ImportResult } from "@/lib/types";

export function LeadImport() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [pending, startTransition] = useTransition();
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  function submit(file: File) {
    setError(null);
    setResult(null);
    startTransition(async () => {
      try {
        setResult(await api.importLeads(file));
        router.refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  return (
    <div className="rounded-[var(--radius)] border border-line bg-surface p-5 shadow-[var(--shadow)]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-[family-name:var(--font-display)] text-base text-ink">
            Import leads
          </h2>
          <p className="mt-1 text-xs text-muted">
            CSV with an <code>email</code> column. Also reads first_name, last_name, title,
            company, domain, linkedin, timezone. Suppressed and duplicate addresses are
            dropped at the door; the rest queue for MX verification.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            ref={inputRef}
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) submit(file);
              e.target.value = "";
            }}
            className="hidden"
          />
          <button
            type="button"
            disabled={pending}
            onClick={() => inputRef.current?.click()}
            className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {pending ? "Importing…" : "Choose CSV"}
          </button>
        </div>
      </div>

      {result ? (
        <div className="mt-3 rounded-lg border border-line bg-bg px-3 py-2 text-xs text-muted">
          Imported <span className="font-semibold text-ink">{result.imported}</span> ·
          deduped <span className="font-semibold text-ink">{result.deduped}</span> ·
          suppressed <span className="font-semibold text-ink">{result.suppressed}</span>
          {result.errors?.length ? (
            <ul className="mt-1 grid gap-0.5">
              {result.errors.map((e) => (
                <li key={e} className="text-amber-600">
                  {e}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {error ? <p className="mt-3 text-xs font-medium text-red-600">{error}</p> : null}
    </div>
  );
}
