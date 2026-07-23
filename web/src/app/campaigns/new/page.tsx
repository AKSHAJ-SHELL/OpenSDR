"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";

const inputCls =
  "mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-ink";
const labelCls =
  "block text-[11px] font-semibold uppercase tracking-[0.08em] text-faint";

export default function NewCampaignPage() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "",
    icp_description: "",
    value_prop: "",
    daily_cap: "50",
    persona_name: "",
    persona_title: "",
    persona_company: "",
    persona_calendly: "",
  });
  const [waits, setWaits] = useState<string[]>(["0", "3", "4"]);

  const ready = form.name.trim() && form.icp_description.trim() && form.value_prop.trim();

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    startTransition(async () => {
      try {
        const campaign = await api.createCampaign({
          name: form.name.trim(),
          icp_description: form.icp_description.trim(),
          value_prop: form.value_prop.trim(),
          daily_cap: Number(form.daily_cap) || 50,
          sender_persona: {
            name: form.persona_name,
            title: form.persona_title,
            company: form.persona_company,
            calendly: form.persona_calendly,
          },
          steps: waits.map((w) => Number(w) || 0),
        });
        router.replace(`/campaigns/${campaign.id}`);
        router.refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    });
  }

  function field(key: keyof typeof form, label: string, props?: object) {
    return (
      <label className="block">
        <span className={labelCls}>{label}</span>
        <input
          value={form[key]}
          onChange={(e) => setForm({ ...form, [key]: e.target.value })}
          className={inputCls}
          {...props}
        />
      </label>
    );
  }

  return (
    <>
      <PageHeader
        title="New campaign"
        subtitle="Define who this is for and why they should care. Steps and variants come next."
      />
      <form
        onSubmit={submit}
        className="max-w-2xl rounded-[var(--radius)] border border-line bg-surface p-6 shadow-[var(--shadow)] animate-rise-delay-1"
      >
        <div className="grid gap-4 md:grid-cols-2">
          {field("name", "Name", { placeholder: "devtools-founders-q3" })}
          {field("daily_cap", "Daily send cap", { type: "number", min: 1 })}
          <label className="block md:col-span-2">
            <span className={labelCls}>ICP description</span>
            <textarea
              value={form.icp_description}
              onChange={(e) => setForm({ ...form, icp_description: e.target.value })}
              rows={2}
              placeholder="Seed-stage devtools founders hiring their first platform engineers"
              className={inputCls}
            />
            <span className="mt-1 block text-[11px] text-faint">
              Embedded and matched against every verified lead — this decides who gets enrolled.
            </span>
          </label>
          <label className="block md:col-span-2">
            <span className={labelCls}>Value prop</span>
            <textarea
              value={form.value_prop}
              onChange={(e) => setForm({ ...form, value_prop: e.target.value })}
              rows={2}
              placeholder="We cut CI times in half without changing your pipeline config"
              className={inputCls}
            />
          </label>
          {field("persona_name", "Sender name")}
          {field("persona_title", "Sender title")}
          {field("persona_company", "Sender company")}
          {field("persona_calendly", "Scheduling link")}
        </div>

        <div className="mt-6">
          <span className={labelCls}>Sequence steps</span>
          <p className="mt-1 text-[11px] text-faint">
            Wait (business days) before each step sends. The first is usually 0.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {waits.map((w, i) => (
              <span key={i} className="flex items-center gap-1">
                <span className="text-xs text-muted">Step {i + 1}:</span>
                <input
                  type="number"
                  min={0}
                  value={w}
                  onChange={(e) =>
                    setWaits(waits.map((x, j) => (j === i ? e.target.value : x)))
                  }
                  className="w-16 rounded-lg border border-line bg-bg px-2 py-1 text-sm text-ink outline-none focus:border-ink"
                />
                {waits.length > 1 ? (
                  <button
                    type="button"
                    aria-label={`Remove step ${i + 1}`}
                    onClick={() => setWaits(waits.filter((_, j) => j !== i))}
                    className="px-1 text-faint transition-colors hover:text-ink"
                  >
                    ×
                  </button>
                ) : null}
              </span>
            ))}
            <button
              type="button"
              onClick={() => setWaits([...waits, "3"])}
              className="rounded-lg border border-line bg-bg px-2 py-1 text-xs font-semibold text-ink transition-colors hover:border-ink"
            >
              + step
            </button>
          </div>
        </div>

        {error ? <p className="mt-4 text-xs font-medium text-red-600">{error}</p> : null}

        <button
          type="submit"
          disabled={pending || !ready}
          className="mt-6 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {pending ? "Creating…" : "Create campaign"}
        </button>
      </form>
    </>
  );
}
