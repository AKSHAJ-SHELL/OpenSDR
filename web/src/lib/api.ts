import type {
  ArmPosterior,
  Campaign,
  InboxMessage,
  Lead,
  Mailbox,
  Overview,
  ReviewItem,
} from "./types";

/** Server-side can use API_URL (e.g. http://api:8000 in Docker). Browser uses NEXT_PUBLIC_API_URL. */
const BASE =
  process.env.API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://127.0.0.1:8000";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText} for ${path}`);
  }
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  overview: () => get<Overview>("/analytics/overview"),
  leads: (status?: string) =>
    get<Lead[]>(status ? `/leads?status=${encodeURIComponent(status)}` : "/leads"),
  inbox: (label?: string) =>
    get<InboxMessage[]>(label ? `/inbox?label=${encodeURIComponent(label)}` : "/inbox"),
  review: () => get<ReviewItem[]>("/inbox/review"),
  campaigns: () => get<Campaign[]>("/campaigns"),
  mailboxes: () => get<Mailbox[]>("/mailboxes"),
  bandit: (campaignId: string) => get<ArmPosterior[]>(`/campaigns/${campaignId}/bandit`),
  activate: (campaignId: string) => post<Campaign>(`/campaigns/${campaignId}/activate`),
  pause: (campaignId: string) => post<Campaign>(`/campaigns/${campaignId}/pause`),
  reclassify: (msgId: string, label: string) =>
    post<InboxMessage>(`/inbox/${msgId}/reclassify`, { label }),
};

export function apiBase() {
  return BASE;
}
