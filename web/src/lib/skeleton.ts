import type { SenderPersona } from "./types";

/**
 * Client-side mirror of the skeleton slot contract in craftsman/copywriter/fill.py.
 * The four LLM slots are fixed by the SlotFill schema; first_name and signature are
 * filled statically at send time. Anything else fails the send — the server 422s it
 * at authoring time, and this mirror lets the editor flag it before submit.
 */
export const LLM_SLOTS = [
  "subject_hook",
  "personalization_sentence",
  "value_prop_bridge",
  "cta_question",
] as const;

export const STATIC_SLOTS = ["first_name", "signature"] as const;

export const KNOWN_SLOTS: readonly string[] = [...LLM_SLOTS, ...STATIC_SLOTS];

export function skeletonSlots(skeleton: string): string[] {
  return [...skeleton.matchAll(/{{(\w+)}}/g)].map((m) => m[1]);
}

export type SkeletonCheck = {
  /** Unknown placeholders — the server rejects these; blocking. */
  errors: string[];
  /** Advisory: missing subject line / no LLM slots used. */
  warnings: string[];
};

export function checkSkeleton(skeleton: string): SkeletonCheck {
  const slots = skeletonSlots(skeleton);
  const unknown = [...new Set(slots.filter((s) => !KNOWN_SLOTS.includes(s)))];
  const errors = unknown.map(
    (s) => `Unknown slot {{${s}}} — the pipeline would fail at send time.`,
  );
  const warnings: string[] = [];
  if (!/^subject:/im.test(skeleton)) {
    warnings.push("No “Subject:” line — the email would send with an empty subject.");
  }
  if (!slots.some((s) => (LLM_SLOTS as readonly string[]).includes(s))) {
    warnings.push("No LLM slots used — every recipient would get identical static text.");
  }
  return { errors, warnings };
}

const SAMPLE_FILLS: Record<string, string> = {
  subject_hook: "your CI queue at Acme",
  personalization_sentence:
    "Saw Acme's careers page lists three new platform-engineer roles this month.",
  value_prop_bridge: "Teams hiring that fast usually feel it first in CI wait times.",
  cta_question: "Worth a look?",
};

/** Same substitution rule as render_skeleton, with canned sample fills. */
export function previewSkeleton(skeleton: string, persona: SenderPersona | null): string {
  const signature = [persona?.name, persona?.title, persona?.company, persona?.calendly]
    .filter(Boolean)
    .join("\n");
  const fills: Record<string, string> = {
    ...SAMPLE_FILLS,
    first_name: "Jordan",
    signature: signature || "—",
  };
  return skeleton.replace(/{{(\w+)}}/g, (match, slot: string) => fills[slot] ?? match);
}
