export type Overview = {
  sent: number;
  replies: number;
  interested: number;
  reply_rate: number;
  copywriter_rejections: number;
  enrollment_states: Record<string, number>;
  lead_statuses: Record<string, number>;
};

export type Lead = {
  id: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
  title: string | null;
  status: string;
  icp_score: number | null;
  email_verified: boolean;
  source: string | null;
};

export type InboxMessage = {
  id: string;
  direction: string;
  subject: string | null;
  body: string | null;
  classification: string | null;
  classification_confidence: number | null;
  sent_at: string | null;
  lead_email: string | null;
  lead_name: string | null;
  company_domain: string | null;
};

export type Campaign = {
  id: string;
  name: string;
  status: string;
  daily_cap: number;
  icp_description?: string | null;
  value_prop?: string | null;
};

export type Mailbox = {
  id: string;
  email: string;
  daily_limit: number;
  sent_today: number;
  warmup_stage: number;
  health: string;
};

export type ArmPosterior = {
  variant_id: string;
  name: string | null;
  step_order: number;
  alpha: number;
  beta: number;
  active: boolean;
  trials: number;
  posterior_mean: number;
};

export type ReviewItem = {
  id: string;
  kind: string;
  payload: Record<string, unknown> | null;
  created_at: string | null;
};
