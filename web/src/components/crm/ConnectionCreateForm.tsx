"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { api } from "@/lib/api";
import type { CRMProvider } from "@/lib/types";

const inputCls =
  "w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-ink";
const labelCls =
  "block text-[11px] font-semibold uppercase tracking-[0.08em] text-faint";

const EMPTY_CREDS: Record<CRMProvider, Record<string, string>> = {
  hubspot: { access_token: "" },
  salesforce: { instance_url: "", client_id: "", client_secret: "" },
};

/** Credential fields per provider. Everything typed here is write-only: the
 *  API encrypts on save and never returns it, and this form clears on success. */
const CRED_FIELDS: Record<
  CRMProvider,
  { key: string; label: string; type: string; placeholder?: string }[]
> = {
  hubspot: [
    { key: "access_token", label: "Private app access token", type: "password" },
  ],
  salesforce: [
    {
      key: "instance_url",
      label: "Instance URL",
      type: "url",
      placeholder: "https://yourorg.my.salesforce.com",
    },
    { key: "client_id", label: "Client ID", type: "text" },
    { key: "client_secret", label: "Client secret", type: "password" },
  ],
};

/** Connect a CRM: provider + name + provider-specific credentials (owner-only). */
export function ConnectionCreateForm() {
  const router = useRouter();
  const [provider, setProvider] = useState<CRMProvider>("hubspot");
  const [name, setName] = useState("");
  const [creds, setCreds] = useState<Record<string, string>>({
    ...EMPTY_CREDS.hubspot,
  });
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const fields = CRED_FIELDS[provider];
  const missing = fields.some((f) => !creds[f.key]?.trim());

  function switchProvider(next: CRMProvider) {
    setProvider(next);
    setCreds({ ...EMPTY_CREDS[next] });
    setError(null);
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (missing || !name.trim()) return;
    setError(null);
    startTransition(async () => {
      try {
        const credentials = Object.fromEntries(
          fields.map((f) => [f.key, creds[f.key].trim()]),
        );
        await api.createCrmConnection({ provider, name: name.trim(), credentials });
        // write-only: drop every credential from state the moment it is saved
        setName("");
        setCreds({ ...EMPTY_CREDS[provider] });
        router.refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    });
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-[var(--radius)] border border-line bg-surface p-5 shadow-[var(--shadow)]"
    >
      <div className="text-sm font-semibold text-ink">Connect a CRM</div>
      <p className="mt-1 text-xs text-muted">
        Credentials are validated, encrypted at rest, and never displayed again —
        to change them later, rotate them on the connection.
      </p>

      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label htmlFor="crm-provider" className={labelCls}>
            Provider
          </label>
          <select
            id="crm-provider"
            value={provider}
            onChange={(e) => switchProvider(e.target.value as CRMProvider)}
            className={`mt-2 ${inputCls}`}
          >
            <option value="hubspot">hubspot</option>
            <option value="salesforce">salesforce</option>
          </select>
        </div>
        <div>
          <label htmlFor="crm-name" className={labelCls}>
            Name
          </label>
          <input
            id="crm-name"
            type="text"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Production HubSpot"
            className={`mt-2 ${inputCls}`}
          />
        </div>
        {fields.map((f) => (
          <div key={f.key}>
            <label htmlFor={`crm-cred-${f.key}`} className={labelCls}>
              {f.label}
            </label>
            <input
              id={`crm-cred-${f.key}`}
              type={f.type}
              required
              autoComplete="off"
              value={creds[f.key] ?? ""}
              onChange={(e) => setCreds({ ...creds, [f.key]: e.target.value })}
              placeholder={f.placeholder}
              className={`mt-2 ${inputCls}`}
            />
          </div>
        ))}
      </div>

      {error ? (
        <p className="mt-3 text-xs font-medium text-red-600">{error}</p>
      ) : null}

      <button
        type="submit"
        disabled={pending || !name.trim() || missing}
        className="mt-4 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
      >
        {pending ? "Connecting…" : "Connect"}
      </button>
    </form>
  );
}
