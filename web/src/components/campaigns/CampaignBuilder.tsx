"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { api } from "@/lib/api";
import type { CampaignDetail, Channel, Step, VariantDetail } from "@/lib/types";
import type { SkeletonChannel } from "@/lib/skeleton";
import { SkeletonEditor, skeletonHasBlockingErrors } from "./SkeletonEditor";

const DEFAULT_SKELETON = `Subject: {{subject_hook}}

Hi {{first_name}},

{{personalization_sentence}}

{{value_prop_bridge}} {{cta_question}}

{{signature}}`;

const DEFAULT_LINKEDIN_SKELETON =
  "Hi {{first_name}}, {{personalization_hook}} {{value_bridge}} {{cta_question}}";

const CHANNEL_LABELS: Record<Channel, string> = {
  email: "Email · autonomous",
  linkedin_task: "LinkedIn · human task",
  call_task: "Call · human task",
};

function defaultSkeletonFor(channel: Channel): string {
  return channel === "linkedin_task" ? DEFAULT_LINKEDIN_SKELETON : DEFAULT_SKELETON;
}

function skeletonChannel(channel: Channel): SkeletonChannel {
  return channel === "linkedin_task" ? "linkedin_task" : "email";
}

const inputCls =
  "mt-1 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-ink";
const labelCls =
  "block text-[11px] font-semibold uppercase tracking-[0.08em] text-faint";
const primaryBtn =
  "rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-50";
const ghostBtn =
  "rounded-lg border border-line bg-bg px-3 py-1.5 text-xs font-semibold text-ink transition-colors hover:border-ink disabled:opacity-50";

function useAction() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function run(fn: () => Promise<unknown>, after?: () => void) {
    setError(null);
    startTransition(async () => {
      try {
        await fn();
        after?.();
        router.refresh();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    });
  }
  return { pending, error, run };
}

function ActionError({ error }: { error: string | null }) {
  if (!error) return null;
  return <p className="mt-2 text-xs font-medium text-red-600">{error}</p>;
}

// ---------------------------------------------------------------- campaign fields

function FieldsCard({ campaign }: { campaign: CampaignDetail }) {
  const persona = campaign.sender_persona ?? {};
  const [form, setForm] = useState({
    name: campaign.name,
    icp_description: campaign.icp_description ?? "",
    value_prop: campaign.value_prop ?? "",
    daily_cap: campaign.daily_cap,
    persona_name: persona.name ?? "",
    persona_title: persona.title ?? "",
    persona_company: persona.company ?? "",
    persona_calendly: persona.calendly ?? "",
    scheduling_url: campaign.scheduling_url ?? "",
    info_doc_url: campaign.info_doc_url ?? "",
  });
  const { pending, error, run } = useAction();

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
    <section className="rounded-[var(--radius)] border border-line bg-surface p-6 shadow-[var(--shadow)]">
      <h2 className="font-[family-name:var(--font-display)] text-lg text-ink">Campaign</h2>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        {field("name", "Name")}
        {field("daily_cap", "Daily cap", { type: "number", min: 1 })}
        <label className="block md:col-span-2">
          <span className={labelCls}>ICP description</span>
          <textarea
            value={form.icp_description}
            onChange={(e) => setForm({ ...form, icp_description: e.target.value })}
            rows={2}
            className={inputCls}
          />
          <span className="mt-1 block text-[11px] text-faint">
            Changing this re-embeds the ICP and changes who scores above the enrollment
            threshold on the next activate.
          </span>
        </label>
        <label className="block md:col-span-2">
          <span className={labelCls}>Value prop</span>
          <textarea
            value={form.value_prop}
            onChange={(e) => setForm({ ...form, value_prop: e.target.value })}
            rows={2}
            className={inputCls}
          />
        </label>
        {field("persona_name", "Sender name")}
        {field("persona_title", "Sender title")}
        {field("persona_company", "Sender company")}
        {field("persona_calendly", "Signature scheduling link")}
        <label className="block">
          <span className={labelCls}>Booking link (reply drafts)</span>
          <input
            value={form.scheduling_url}
            onChange={(e) => setForm({ ...form, scheduling_url: e.target.value })}
            className={inputCls}
            placeholder="https://cal.com/you/15min"
          />
          <span className="mt-1 block text-[11px] text-faint">
            Embedded verbatim in interested-reply drafts; bookings flow back via the
            Cal.com webhook.
          </span>
        </label>
        <label className="block">
          <span className={labelCls}>One-pager link (info replies)</span>
          <input
            value={form.info_doc_url}
            onChange={(e) => setForm({ ...form, info_doc_url: e.target.value })}
            className={inputCls}
            placeholder="https://yoursite.com/one-pager.pdf"
          />
          <span className="mt-1 block text-[11px] text-faint">
            The approved document for &quot;send me info&quot; replies. Without it,
            Autopilot always escalates those.
          </span>
        </label>
      </div>
      <div className="mt-4">
        <button
          type="button"
          disabled={pending || !form.name.trim()}
          onClick={() =>
            run(() =>
              api.updateCampaign(campaign.id, {
                name: form.name,
                icp_description: form.icp_description,
                value_prop: form.value_prop,
                daily_cap: Number(form.daily_cap) || campaign.daily_cap,
                sender_persona: {
                  name: form.persona_name,
                  title: form.persona_title,
                  company: form.persona_company,
                  calendly: form.persona_calendly,
                },
                scheduling_url: form.scheduling_url || null,
                info_doc_url: form.info_doc_url || null,
              }),
            )
          }
          className={primaryBtn}
        >
          {pending ? "Saving…" : "Save changes"}
        </button>
        <ActionError error={error} />
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- variants

function VariantCard({
  campaign,
  variant,
  channel,
  onClone,
}: {
  campaign: CampaignDetail;
  variant: VariantDetail;
  channel: Channel;
  onClone: (skeleton: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [skeleton, setSkeleton] = useState(variant.skeleton);
  const { pending, error, run } = useAction();
  const frozen = variant.trials > 0;

  return (
    <div className="rounded-lg border border-line bg-bg p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-ink">{variant.name ?? "unnamed"}</span>
          <span className="text-[11px] text-faint">
            {variant.trials} trial{variant.trials === 1 ? "" : "s"} · α {variant.alpha} · β{" "}
            {variant.beta}
          </span>
          {!variant.active ? (
            <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-faint">
              inactive
            </span>
          ) : null}
        </div>
        <div className="flex gap-2">
          {frozen ? (
            <button type="button" onClick={() => onClone(variant.skeleton)} className={ghostBtn}>
              Clone as new variant
            </button>
          ) : (
            <button
              type="button"
              onClick={() => {
                setSkeleton(variant.skeleton);
                setEditing(!editing);
              }}
              className={ghostBtn}
            >
              {editing ? "Cancel" : "Edit skeleton"}
            </button>
          )}
          <button
            type="button"
            disabled={pending}
            onClick={() =>
              run(() => api.updateVariant(campaign.id, variant.id, { active: !variant.active }))
            }
            className={ghostBtn}
          >
            {variant.active ? "Deactivate" : "Reactivate"}
          </button>
        </div>
      </div>

      {frozen ? (
        <p className="mt-2 text-[11px] text-faint">
          This arm has recorded trials, so its skeleton is frozen — the posterior measured
          exactly this copy. Clone it to iterate.
        </p>
      ) : null}

      {editing ? (
        <div className="mt-3">
          <SkeletonEditor
            value={skeleton}
            onChange={setSkeleton}
            persona={campaign.sender_persona}
            channel={skeletonChannel(channel)}
          />
          <button
            type="button"
            disabled={pending || skeletonHasBlockingErrors(skeleton, skeletonChannel(channel))}
            onClick={() =>
              run(
                () => api.updateVariant(campaign.id, variant.id, { skeleton }),
                () => setEditing(false),
              )
            }
            className={`mt-2 ${primaryBtn}`}
          >
            {pending ? "Saving…" : "Save skeleton"}
          </button>
        </div>
      ) : (
        <pre className="mt-3 whitespace-pre-wrap rounded-lg border border-dashed border-line px-3 py-2 font-mono text-xs leading-relaxed text-muted">
          {variant.skeleton}
        </pre>
      )}
      <ActionError error={error} />
    </div>
  );
}

function AddVariantForm({
  campaign,
  step,
  initialSkeleton,
  onDone,
}: {
  campaign: CampaignDetail;
  step: Step;
  initialSkeleton: string;
  onDone: () => void;
}) {
  const [name, setName] = useState("");
  const [skeleton, setSkeleton] = useState(initialSkeleton);
  const { pending, error, run } = useAction();

  return (
    <div className="mt-3 rounded-lg border border-line bg-bg p-4">
      <label className="block max-w-xs">
        <span className={labelCls}>Variant name</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="pain_led"
          className={inputCls}
        />
      </label>
      <div className="mt-3">
        <SkeletonEditor
          value={skeleton}
          onChange={setSkeleton}
          persona={campaign.sender_persona}
          channel={skeletonChannel(step.channel)}
        />
      </div>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={
            pending || !name.trim() || skeletonHasBlockingErrors(skeleton, skeletonChannel(step.channel))
          }
          onClick={() =>
            run(
              () =>
                api.addVariant(campaign.id, {
                  step_order: step.step_order,
                  name: name.trim(),
                  skeleton,
                }),
              onDone,
            )
          }
          className={primaryBtn}
        >
          {pending ? "Adding…" : "Add variant"}
        </button>
        <button type="button" onClick={onDone} className={ghostBtn}>
          Cancel
        </button>
      </div>
      <ActionError error={error} />
    </div>
  );
}

// ---------------------------------------------------------------- steps

function StepCard({ campaign, step }: { campaign: CampaignDetail; step: Step }) {
  const [waitDays, setWaitDays] = useState(String(step.wait_days));
  const [adding, setAdding] = useState(false);
  const [addSkeleton, setAddSkeleton] = useState(defaultSkeletonFor(step.channel));
  const { pending, error, run } = useAction();
  const structureFrozen = campaign.enrollments > 0;
  const isTask = step.channel !== "email";
  const usesSkeleton = step.channel !== "call_task";

  return (
    <div className="rounded-[var(--radius)] border border-line bg-surface p-5 shadow-[var(--shadow)]">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h3 className="font-[family-name:var(--font-display)] text-base text-ink">
            Step {step.step_order}
          </h3>
          <select
            value={step.channel}
            disabled={pending || structureFrozen || step.variants.length > 0}
            title={
              structureFrozen
                ? "Structure is frozen while leads are enrolled"
                : step.variants.length > 0
                  ? "Delete this step's variants before changing the channel — skeletons don't port between channels"
                  : undefined
            }
            onChange={(e) =>
              run(() =>
                api.updateStep(campaign.id, step.id, { channel: e.target.value as Channel }),
              )
            }
            className="rounded-lg border border-line bg-bg px-2 py-1 text-xs font-medium text-ink outline-none focus:border-ink disabled:opacity-60"
          >
            {(Object.keys(CHANNEL_LABELS) as Channel[]).map((c) => (
              <option key={c} value={c}>
                {CHANNEL_LABELS[c]}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-muted">
            Wait
            <input
              type="number"
              min={0}
              value={waitDays}
              onChange={(e) => setWaitDays(e.target.value)}
              className="w-16 rounded-lg border border-line bg-bg px-2 py-1 text-sm text-ink outline-none focus:border-ink"
            />
            business days
          </label>
          {Number(waitDays) !== step.wait_days ? (
            <button
              type="button"
              disabled={pending}
              onClick={() =>
                run(() =>
                  api.updateStep(campaign.id, step.id, { wait_days: Number(waitDays) }),
                )
              }
              className={primaryBtn}
            >
              Save
            </button>
          ) : null}
          <button
            type="button"
            disabled={pending || structureFrozen}
            title={structureFrozen ? "Structure is frozen while leads are enrolled" : undefined}
            onClick={() => run(() => api.deleteStep(campaign.id, step.id))}
            className={ghostBtn}
          >
            Delete step
          </button>
        </div>
      </div>

      {isTask ? (
        <label className="mt-3 flex items-start gap-2 text-xs text-muted">
          <input
            type="checkbox"
            checked={step.skip_on_expire}
            disabled={pending}
            onChange={(e) =>
              run(() =>
                api.updateStep(campaign.id, step.id, { skip_on_expire: e.target.checked }),
              )
            }
            className="mt-0.5"
          />
          <span>
            Auto-skip when nobody does the task — after the due window the sequence advances
            without the touch. Off (default): the sequence holds and the task shows as
            overdue until a human acts.
          </span>
        </label>
      ) : null}

      {usesSkeleton ? (
        <>
          <div className="mt-4 grid gap-3">
            {step.variants.map((v) => (
              <VariantCard
                key={v.id}
                campaign={campaign}
                variant={v}
                channel={step.channel}
                onClone={(skeleton) => {
                  setAddSkeleton(skeleton);
                  setAdding(true);
                }}
              />
            ))}
            {step.variants.length === 0 ? (
              <p className="text-xs text-faint">
                No variants yet — the campaign cannot activate until every step has at least one.
              </p>
            ) : null}
          </div>

          {adding ? (
            <AddVariantForm
              campaign={campaign}
              step={step}
              initialSkeleton={addSkeleton}
              onDone={() => {
                setAdding(false);
                setAddSkeleton(defaultSkeletonFor(step.channel));
              }}
            />
          ) : (
            <button type="button" onClick={() => setAdding(true)} className={`mt-3 ${ghostBtn}`}>
              Add variant
            </button>
          )}
        </>
      ) : (
        <p className="mt-4 text-xs leading-relaxed text-faint">
          Call steps need no variants. Craftsman generates a grounded call brief (opener,
          pain hypotheses, objection notes) from the research brief and queues it on the
          Tasks page for a human to call.
        </p>
      )}
      <ActionError error={error} />
    </div>
  );
}

function AddStep({ campaign }: { campaign: CampaignDetail }) {
  const [waitDays, setWaitDays] = useState("3");
  const [channel, setChannel] = useState<Channel>("email");
  const { pending, error, run } = useAction();
  const structureFrozen = campaign.enrollments > 0;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={pending || structureFrozen}
        title={structureFrozen ? "Structure is frozen while leads are enrolled" : undefined}
        onClick={() => run(() => api.addStep(campaign.id, Number(waitDays) || 0, channel))}
        className={ghostBtn}
      >
        Add step
      </button>
      <select
        value={channel}
        onChange={(e) => setChannel(e.target.value as Channel)}
        className="rounded-lg border border-line bg-bg px-2 py-1.5 text-xs font-medium text-ink outline-none focus:border-ink"
      >
        {(Object.keys(CHANNEL_LABELS) as Channel[]).map((c) => (
          <option key={c} value={c}>
            {CHANNEL_LABELS[c]}
          </option>
        ))}
      </select>
      <label className="flex items-center gap-2 text-xs text-muted">
        after waiting
        <input
          type="number"
          min={0}
          value={waitDays}
          onChange={(e) => setWaitDays(e.target.value)}
          className="w-16 rounded-lg border border-line bg-bg px-2 py-1 text-sm text-ink outline-none focus:border-ink"
        />
        business days
      </label>
      <ActionError error={error} />
    </div>
  );
}

// ---------------------------------------------------------------- page assembly

export function CampaignBuilder({ campaign }: { campaign: CampaignDetail }) {
  return (
    <div className="grid gap-6">
      {campaign.enrollments > 0 ? (
        <p className="rounded-lg border border-line bg-surface px-4 py-3 text-xs text-muted">
          {campaign.enrollments} lead{campaign.enrollments === 1 ? " is" : "s are"} enrolled —
          sequence structure is frozen (wait times and variants stay editable).
        </p>
      ) : null}
      <FieldsCard campaign={campaign} />
      <section>
        <h2 className="mb-3 font-[family-name:var(--font-display)] text-lg text-ink">Sequence</h2>
        <div className="grid gap-4">
          {campaign.steps.map((step) => (
            <StepCard key={step.id} campaign={campaign} step={step} />
          ))}
          <AddStep campaign={campaign} />
        </div>
      </section>
    </div>
  );
}
