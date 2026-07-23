import type { SenderPersona } from "./types";

/**
 * Client-side mirror of the per-channel skeleton slot contract
 * (craftsman/channels.py). Email slots are fixed by the SlotFill schema,
 * LinkedIn slots by LinkedInSlotFill; static slots are filled at generation time.
 * Anything else fails the pipeline — the server 422s it at authoring time, and
 * this mirror lets the editor flag it before submit. call_task has no skeleton
 * (structured brief).
 */
export type SkeletonChannel = "email" | "linkedin_task";

export const LLM_SLOTS_BY_CHANNEL: Record<SkeletonChannel, readonly string[]> = {
  email: [
    "subject_hook",
    "personalization_sentence",
    "value_prop_bridge",
    "cta_question",
  ],
  linkedin_task: ["personalization_hook", "value_bridge", "cta_question"],
};

export const STATIC_SLOTS_BY_CHANNEL: Record<SkeletonChannel, readonly string[]> = {
  email: ["first_name", "signature"],
  // no signature on LinkedIn — the profile is the signature
  linkedin_task: ["first_name"],
};

// Back-compat aliases (email vocabulary), still used by non-channel-aware callers.
export const LLM_SLOTS = LLM_SLOTS_BY_CHANNEL.email;
export const STATIC_SLOTS = STATIC_SLOTS_BY_CHANNEL.email;
export const KNOWN_SLOTS: readonly string[] = [...LLM_SLOTS, ...STATIC_SLOTS];

export function knownSlots(channel: SkeletonChannel): readonly string[] {
  return [...LLM_SLOTS_BY_CHANNEL[channel], ...STATIC_SLOTS_BY_CHANNEL[channel]];
}

export function skeletonSlots(skeleton: string): string[] {
  return [...skeleton.matchAll(/{{(\w+)}}/g)].map((m) => m[1]);
}

export type SkeletonCheck = {
  /** Unknown placeholders — the server rejects these; blocking. */
  errors: string[];
  /** Advisory: missing subject line / no LLM slots used. */
  warnings: string[];
};

export function checkSkeleton(
  skeleton: string,
  channel: SkeletonChannel = "email",
): SkeletonCheck {
  const slots = skeletonSlots(skeleton);
  const known = knownSlots(channel);
  const llm = LLM_SLOTS_BY_CHANNEL[channel];
  const unknown = [...new Set(slots.filter((s) => !known.includes(s)))];
  const errors = unknown.map(
    (s) => `Unknown slot {{${s}}} for this channel — the pipeline would fail.`,
  );
  const warnings: string[] = [];
  if (channel === "email" && !/^subject:/im.test(skeleton)) {
    warnings.push("No “Subject:” line — the email would send with an empty subject.");
  }
  if (!slots.some((s) => llm.includes(s))) {
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
  // linkedin
  personalization_hook: "saw Acme is hiring three platform engineers this month.",
  value_bridge: "We cut CI wait times for teams growing that fast.",
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
