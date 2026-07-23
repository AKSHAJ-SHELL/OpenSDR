import type {
  ArmPosterior,
  Campaign,
  InboxMessage,
  Lead,
  Mailbox,
  Overview,
  ReviewItem,
} from "./types";

const IS_SERVER = typeof window === "undefined";

/**
 * On the server, call the API directly and attach the API key.
 * In the browser, route through the session-gated Next proxy — the key stays
 * server-side and is never shipped to the client.
 */
const API_BASE =
  process.env.API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://127.0.0.1:8000";

function target(path: string): string {
  return IS_SERVER ? `${API_BASE}${path}` : `/api/proxy${path}`;
}

function authHeaders(): Record<string, string> {
  if (IS_SERVER && process.env.CRAFTSMAN_API_KEY) {
    return { Authorization: `Bearer ${process.env.CRAFTSMAN_API_KEY}` };
  }
  return {};
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(target(path), {
    cache: "no-store",
    headers: { Accept: "application/json", ...authHeaders() },
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText} for ${path}`);
  }
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(target(path), {
    method: "POST",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...authHeaders(),
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
  return API_BASE;
}
