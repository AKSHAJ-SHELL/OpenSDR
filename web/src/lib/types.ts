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
  icp_cosine: number | null;
  icp_rule: number | null;
  icp_scored_at: string | null;
  icp_scored_campaign_id: string | null;
  icp_scored_campaign_name: string | null;
  icp_matched_keyword: string | null;
};

export type ImportResult = {
  imported: number;
  deduped: number;
  suppressed: number;
  errors: string[];
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
  dkim_selector: string | null;
  daily_limit: number;
  sent_today: number;
  warmup_stage: number;
  health: string;
};

export type DnsStatus = "pass" | "missing" | "error";

export type DnsRecord = {
  status: DnsStatus;
  record: string | null;
  recommended: string | null;
};

export type DmarcRecord = DnsRecord & { policy: string | null };

export type DkimRecord = {
  status: DnsStatus;
  selector: string | null;
  record: string | null;
};

export type WarmupStep = { day: number; stage: number; cap: number };

export type Warmup = {
  stage: number;
  effective_cap: number;
  daily_limit: number;
  sent_today: number;
  advances_per_day: number;
  schedule: WarmupStep[];
};

export type DeliverabilityReport = {
  mailbox_id: string;
  email: string;
  domain: string;
  primary_domain_warning: boolean;
  spf: DnsRecord;
  dmarc: DmarcRecord;
  dkim: DkimRecord;
  warmup: Warmup;
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
  kind: "classification" | "copywriter" | string;
  message_id: string | null;
  enrollment_id: string | null;
  payload: Record<string, unknown> | null;
  created_at: string | null;
  lead_email: string | null;
  lead_name: string | null;
  campaign_name: string | null;
  enrollment_state: string | null;
  message_subject: string | null;
  message_body: string | null;
};

export type ReviewAction = "retry" | "skip" | "kill" | "resolve";
