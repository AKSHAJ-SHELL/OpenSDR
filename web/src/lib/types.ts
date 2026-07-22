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

export type SenderPersona = {
  name?: string;
  title?: string;
  company?: string;
  calendly?: string;
};

export type VariantDetail = {
  id: string;
  name: string | null;
  alpha: number;
  beta: number;
  active: boolean;
  skeleton: string;
  slot_schema: Record<string, string>;
  trials: number;
};

export type Step = {
  id: string;
  step_order: number;
  wait_days: number;
  variants: VariantDetail[];
};

export type CampaignDetail = Campaign & {
  sender_persona: SenderPersona | null;
  enrollments: number;
  steps: Step[];
};

export type CampaignCreate = {
  name: string;
  icp_description: string;
  value_prop: string;
  sender_persona: SenderPersona;
  daily_cap: number;
  steps: number[];
};

export type CampaignUpdate = Partial<
  Pick<Campaign, "name" | "icp_description" | "value_prop" | "daily_cap">
> & { sender_persona?: SenderPersona };

export type VariantUpdate = {
  name?: string;
  active?: boolean;
  skeleton?: string;
};

export type DryRunItem = {
  id: string;
  lead_email: string;
  lead_name: string | null;
  icp_score: number | null;
  variant_name: string | null;
  subject: string | null;
  body: string | null;
  validator_ok: boolean | null;
  validator_errors: string[] | null;
  delivered: boolean;
  error: string | null;
};

export type DryRun = {
  id: string;
  campaign_id: string;
  status: "running" | "complete" | "failed";
  requested_n: number;
  error: string | null;
  created_at: string | null;
  finished_at: string | null;
  items: DryRunItem[];
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
