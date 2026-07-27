/**
 * CRM field-mapping vocabulary for the mapping editor (M5.2).
 *
 * TARGETS and DEFAULT_MAPS are a hardcoded copy mirroring
 * `craftsman/crm/mapping.py` — keep the two in sync by hand. The API
 * validates every overlay against the real thing; this copy only drives
 * the editor UI (default rows, target dropdown, overlay diffing).
 *
 * A connection's `field_map` is an overlay on the provider default:
 * {crm_attr: leadrow_field} remaps, {crm_attr: null} drops the default.
 */
import type { CRMProvider } from "./types";

// LeadRow fields a map may target (email is required; the rest optional)
export const TARGETS = [
  "email",
  "first_name",
  "last_name",
  "title",
  "company_name",
  "company_domain",
  "linkedin_url",
  "timezone",
] as const;

export type MappingTarget = (typeof TARGETS)[number];

export const DEFAULT_MAPS: Record<CRMProvider, Record<string, string>> = {
  hubspot: {
    email: "email",
    firstname: "first_name",
    lastname: "last_name",
    jobtitle: "title",
    company: "company_name",
    website: "company_domain",
    linkedin_url: "linkedin_url",
    hs_timezone: "timezone",
  },
  salesforce: {
    Email: "email",
    FirstName: "first_name",
    LastName: "last_name",
    Title: "title",
    Company: "company_name",
    Website: "company_domain",
    LinkedIn__c: "linkedin_url",
  },
};

export type MapEntry = { src: string; target: string };

/** Provider default with the connection overlay applied, as ordered rows
 *  (defaults first, overlay-added attrs after — matches the API's merge). */
export function effectiveEntries(
  provider: CRMProvider,
  overlay: Record<string, string | null>,
): MapEntry[] {
  const merged = new Map<string, string>(Object.entries(DEFAULT_MAPS[provider]));
  for (const [src, target] of Object.entries(overlay ?? {})) {
    if (!target) merged.delete(src); // null/"" tombstones the default
    else merged.set(src, target);
  }
  return [...merged.entries()].map(([src, target]) => ({ src, target }));
}

/** Diff editor rows back to the overlay the API stores: remaps and additions
 *  as {src: target}, removed defaults as {src: null}. */
export function overlayFromEntries(
  provider: CRMProvider,
  entries: MapEntry[],
): Record<string, string | null> {
  const defaults = DEFAULT_MAPS[provider];
  const bySrc = new Map(entries.map((e) => [e.src, e.target]));
  const overlay: Record<string, string | null> = {};
  for (const [src, target] of Object.entries(defaults)) {
    const now = bySrc.get(src);
    if (now === undefined) overlay[src] = null;
    else if (now !== target) overlay[src] = now;
  }
  for (const { src, target } of entries) {
    if (!(src in defaults)) overlay[src] = target;
  }
  return overlay;
}
