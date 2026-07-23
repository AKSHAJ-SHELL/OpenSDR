import type {
  ArmPosterior,
  Campaign,
  CampaignCreate,
  CampaignDetail,
  CampaignUpdate,
  DeliverabilityReport,
  DryRun,
  ImportResult,
  InboxMessage,
  Lead,
  LeadEnrichment,
  LeadSignal,
  ScoringWeights,
  SignalRule,
  Mailbox,
  Overview,
  ReviewAction,
  ReviewItem,
  SourcedLeadIn,
  SourcedPreview,
  SourceSearchRequest,
  Step,
  VariantDetail,
  VariantUpdate,
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

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(target(path), {
    method,
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
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const post = <T,>(path: string, body?: unknown) => send<T>("POST", path, body);
const patch = <T,>(path: string, body?: unknown) => send<T>("PATCH", path, body);
const del = <T,>(path: string) => send<T>("DELETE", path);

/** Multipart upload. Content-Type is deliberately unset so the browser adds the
 *  boundary; the proxy forwards whatever it receives. */
async function upload<T>(path: string, file: File): Promise<T> {
  const body = new FormData();
  body.append("file", file);
  const res = await fetch(target(path), {
    method: "POST",
    cache: "no-store",
    headers: { Accept: "application/json", ...authHeaders() },
    body,
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  overview: () => get<Overview>("/analytics/overview"),
  leads: (params?: { status?: string; score_gte?: number; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.status) q.set("status", params.status);
    if (params?.score_gte != null) q.set("score_gte", String(params.score_gte));
    if (params?.limit != null) q.set("limit", String(params.limit));
    const qs = q.toString();
    return get<Lead[]>(qs ? `/leads?${qs}` : "/leads");
  },
  importLeads: (file: File) => upload<ImportResult>("/leads/import", file),
  leadEnrichments: (id: string) => get<LeadEnrichment[]>(`/leads/${id}/enrichments`),
  scoringWeights: () => get<ScoringWeights>("/leads/scoring-weights"),
  leadSignals: (id: string) => get<LeadSignal[]>(`/leads/${id}/signals`),
  signalRules: (campaignId: string) => get<SignalRule[]>(`/campaigns/${campaignId}/signal-rules`),
  createSignalRule: (campaignId: string, body: { signal_type: string; action: string }) =>
    post<SignalRule>(`/campaigns/${campaignId}/signal-rules`, body),
  deleteSignalRule: (campaignId: string, ruleId: string) =>
    del<void>(`/campaigns/${campaignId}/signal-rules/${ruleId}`),
  sourceProviders: () => get<string[]>("/leads/source/providers"),
  sourceLeads: (body: SourceSearchRequest) => post<SourcedPreview>("/leads/source", body),
  importSourced: (source: string, leads: SourcedLeadIn[]) =>
    post<ImportResult>("/leads/source/import", { source, leads }),
  suppressLead: (id: string) => post<void>(`/leads/${id}/suppress`),
  eraseLead: (id: string) => del<void>(`/leads/${id}/erase`),
  reviewAction: (itemId: string, action: ReviewAction) =>
    post<{ resolved: boolean; action: string; new_state: string | null }>(
      `/inbox/review/${itemId}/action`,
      { action },
    ),
  inbox: (label?: string) =>
    get<InboxMessage[]>(label ? `/inbox?label=${encodeURIComponent(label)}` : "/inbox"),
  review: () => get<ReviewItem[]>("/inbox/review"),
  campaigns: () => get<Campaign[]>("/campaigns"),
  campaignDetail: (id: string) => get<CampaignDetail>(`/campaigns/${id}`),
  createCampaign: (body: CampaignCreate) => post<Campaign>("/campaigns", body),
  updateCampaign: (id: string, body: CampaignUpdate) =>
    patch<CampaignDetail>(`/campaigns/${id}`, body),
  addStep: (campaignId: string, waitDays: number) =>
    post<Step>(`/campaigns/${campaignId}/steps`, { wait_days: waitDays }),
  updateStep: (campaignId: string, stepId: string, waitDays: number) =>
    patch<Step>(`/campaigns/${campaignId}/steps/${stepId}`, { wait_days: waitDays }),
  deleteStep: (campaignId: string, stepId: string) =>
    del<void>(`/campaigns/${campaignId}/steps/${stepId}`),
  addVariant: (campaignId: string, body: { step_order: number; name: string; skeleton: string }) =>
    post<VariantDetail>(`/campaigns/${campaignId}/variants`, body),
  updateVariant: (campaignId: string, variantId: string, body: VariantUpdate) =>
    patch<VariantDetail>(`/campaigns/${campaignId}/variants/${variantId}`, body),
  startDryRun: (campaignId: string, n: number) =>
    post<DryRun>(`/campaigns/${campaignId}/dry-run`, { n }),
  dryRuns: (campaignId: string) => get<DryRun[]>(`/campaigns/${campaignId}/dry-runs`),
  dryRun: (campaignId: string, runId: string) =>
    get<DryRun>(`/campaigns/${campaignId}/dry-runs/${runId}`),
  mailboxes: () => get<Mailbox[]>("/mailboxes"),
  deliverability: (mailboxId: string) =>
    get<DeliverabilityReport>(`/mailboxes/${mailboxId}/deliverability`),
  bandit: (campaignId: string) => get<ArmPosterior[]>(`/campaigns/${campaignId}/bandit`),
  activate: (campaignId: string) => post<Campaign>(`/campaigns/${campaignId}/activate`),
  pause: (campaignId: string) => post<Campaign>(`/campaigns/${campaignId}/pause`),
  reclassify: (msgId: string, label: string) =>
    post<InboxMessage>(`/inbox/${msgId}/reclassify`, { label }),
};

export function apiBase() {
  return API_BASE;
}
