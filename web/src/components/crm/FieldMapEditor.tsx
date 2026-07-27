"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import {
  DEFAULT_MAPS,
  TARGETS,
  effectiveEntries,
  overlayFromEntries,
  type MapEntry,
} from "@/lib/crm-mapping";
import type { CRMConnection } from "@/lib/types";

const inputCls =
  "w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-ink";
const btn =
  "rounded-lg border border-line bg-bg px-2.5 py-1 text-[11px] font-semibold text-muted transition-colors hover:border-ink hover:text-ink disabled:opacity-50";

/** Edit the connection's field map: the provider default with the overlay
 *  applied, row by row. Saving PATCHes only the overlay (remaps, additions,
 *  and removed defaults as null tombstones). */
export function FieldMapEditor({
  connection,
  canAdmin,
}: {
  connection: CRMConnection;
  canAdmin: boolean;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [entries, setEntries] = useState<MapEntry[]>(() =>
    effectiveEntries(connection.provider, connection.field_map),
  );
  const [newSrc, setNewSrc] = useState("");
  const [newTarget, setNewTarget] = useState<string>(TARGETS[0]);

  const defaults = DEFAULT_MAPS[connection.provider] ?? {};
  const srcCounts = new Map<string, number>();
  for (const e of entries) srcCounts.set(e.src, (srcCounts.get(e.src) ?? 0) + 1);
  const hasDuplicate = [...srcCounts.values()].some((n) => n > 1);
  const hasEmail = entries.some((e) => e.target === "email");
  const dirty =
    JSON.stringify(entries) !==
    JSON.stringify(effectiveEntries(connection.provider, connection.field_map));

  function setTarget(i: number, target: string) {
    setSaved(false);
    setEntries(entries.map((e, j) => (j === i ? { ...e, target } : e)));
  }

  function remove(i: number) {
    setSaved(false);
    setEntries(entries.filter((_, j) => j !== i));
  }

  function add(e: React.FormEvent) {
    e.preventDefault();
    const src = newSrc.trim();
    if (!src || srcCounts.has(src)) return;
    setSaved(false);
    setEntries([...entries, { src, target: newTarget }]);
    setNewSrc("");
  }

  function save() {
    setError(null);
    setSaved(false);
    startTransition(async () => {
      try {
        await api.updateCrmConnection(connection.id, {
          field_map: overlayFromEntries(connection.provider, entries),
        });
        setSaved(true);
        router.refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }

  return (
    <div className="mt-4 rounded-lg border border-line bg-bg p-4">
      <p className="text-[11px] leading-relaxed text-muted">
        Each row maps a <span className="font-semibold">{connection.provider}</span>{" "}
        contact attribute to a lead field. Rows differing from the provider default
        are marked custom; removed defaults stay removed. A map must keep a source
        for <code className="font-mono">email</code>.
      </p>

      <table className="mt-3 w-full text-left text-sm">
        <thead className="border-b border-line text-[11px] uppercase tracking-[0.08em] text-faint">
          <tr>
            <th className="py-2 pr-3 font-semibold">CRM attribute</th>
            <th className="py-2 pr-3 font-semibold">Lead field</th>
            <th className="py-2 pr-3 font-semibold">Origin</th>
            {canAdmin ? <th className="py-2 text-right font-semibold">Actions</th> : null}
          </tr>
        </thead>
        <tbody>
          {entries.map((entry, i) => {
            const isDefault = defaults[entry.src] === entry.target;
            return (
              <tr key={`${entry.src}-${i}`} className="border-b border-line last:border-0">
                <td className="py-2 pr-3">
                  <code className="font-mono text-xs text-ink">{entry.src}</code>
                </td>
                <td className="py-2 pr-3">
                  {canAdmin ? (
                    <select
                      aria-label={`Lead field for ${entry.src}`}
                      value={entry.target}
                      disabled={pending}
                      onChange={(e) => setTarget(i, e.target.value)}
                      className="rounded-lg border border-line bg-surface px-2 py-1 text-xs font-medium text-ink outline-none focus:border-ink disabled:opacity-50"
                    >
                      {TARGETS.map((t) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <code className="font-mono text-xs text-ink">{entry.target}</code>
                  )}
                </td>
                <td className="py-2 pr-3">
                  <Badge tone={isDefault ? "muted" : "accent"}>
                    {isDefault ? "default" : "custom"}
                  </Badge>
                </td>
                {canAdmin ? (
                  <td className="py-2 text-right">
                    <button
                      type="button"
                      disabled={pending}
                      onClick={() => remove(i)}
                      className={btn}
                    >
                      Remove
                    </button>
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>

      {canAdmin ? (
        <>
          <form onSubmit={add} className="mt-3 flex flex-wrap items-end gap-2">
            <div className="w-56">
              <label
                htmlFor={`map-src-${connection.id}`}
                className="block text-[11px] font-semibold uppercase tracking-[0.08em] text-faint"
              >
                Add CRM attribute
              </label>
              <input
                id={`map-src-${connection.id}`}
                type="text"
                value={newSrc}
                onChange={(e) => setNewSrc(e.target.value)}
                placeholder={connection.provider === "hubspot" ? "e.g. mobilephone" : "e.g. Department"}
                className={`mt-2 ${inputCls} font-mono text-xs`}
              />
            </div>
            <select
              aria-label="Lead field for the new attribute"
              value={newTarget}
              onChange={(e) => setNewTarget(e.target.value)}
              className="rounded-lg border border-line bg-surface px-2 py-2 text-xs font-medium text-ink outline-none focus:border-ink"
            >
              {TARGETS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <button
              type="submit"
              disabled={pending || !newSrc.trim() || srcCounts.has(newSrc.trim())}
              className={btn}
            >
              Add
            </button>
            {newSrc.trim() && srcCounts.has(newSrc.trim()) ? (
              <span className="text-[11px] font-medium text-red-600">
                That attribute is already mapped.
              </span>
            ) : null}
          </form>

          <div className="mt-3 flex items-center gap-3 border-t border-line pt-3">
            <button
              type="button"
              disabled={pending || !dirty || hasDuplicate || !hasEmail}
              onClick={save}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {pending ? "Saving…" : "Save mapping"}
            </button>
            {!hasEmail ? (
              <span className="text-xs font-medium text-red-600">
                The map needs a source for email.
              </span>
            ) : saved && !dirty ? (
              <span className="text-xs font-medium text-good">Saved.</span>
            ) : null}
          </div>
        </>
      ) : (
        <p className="mt-3 border-t border-line pt-3 text-[11px] text-faint">
          Changing the mapping is limited to owner sessions.
        </p>
      )}

      {error ? <p className="mt-3 text-xs font-medium text-red-600">{error}</p> : null}
    </div>
  );
}
