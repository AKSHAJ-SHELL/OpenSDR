"""Pydantic schemas: API I/O + the LLM-facing structured-output models."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

# ---------------------------------------------------------------- LLM schemas


class TriggerEvent(BaseModel):
    claim: str
    source_url: str
    approx_date: str


class ResearchBrief(BaseModel):
    what_they_do: str = Field(max_length=280)
    industry: str
    trigger_events: list[TriggerEvent] = Field(default_factory=list)
    likely_pain_points: list[str] = Field(default_factory=list, max_length=3)
    evidence_quotes: list[str] = Field(default_factory=list)


class SlotFill(BaseModel):
    """Copywriter output: only the slots, never a whole email."""

    subject_hook: str
    personalization_sentence: str
    value_prop_bridge: str
    cta_question: str


class LinkedInSlotFill(BaseModel):
    """LinkedIn copywriter output (M3.2): only the slots, never a whole message.
    Rendered through a skeleton and the same four-gate validator with channel caps
    (note ≤ LINKEDIN_NOTE_MAX_CHARS after render)."""

    personalization_hook: str
    value_bridge: str
    cta_question: str


class CallBrief(BaseModel):
    """Call-task output (M3.3): a grounded, structured brief — deliberately NOT a
    script. Every field passes grounding + banned-phrase checks; word caps per field
    are config knobs (⛔ Gate M3 approved defaults 25/20/40)."""

    opener: str
    pain_hypotheses: list[str] = Field(min_length=1, max_length=2)
    objection_notes: str


class ReplyClassification(BaseModel):
    label: Literal["interested", "objection", "not_now", "ooo", "unsubscribe", "bounce_or_auto"]
    ooo_return_date: date | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class ReplyDraftFill(BaseModel):
    """Reply copywriter output (M4.1): only the slots, never a whole reply.

    The LLM also picks WHICH fixed skeleton fits an objection (timing vs info vs
    other) — an enum choice, not free text. `other` objections (pricing, competitor,
    hostile) deliberately get NO draft: a human writes those. Rendered through the
    reply-specific validator (grounding incl. the inbound reply text, commitment
    gate, ≤ REPLY_DRAFT_MAX_WORDS, grade ≤ 8)."""

    objection_kind: Literal["timing", "info", "other"] = "other"
    acknowledgment: str
    answer_bridge: str
    cta_question: str


# ---------------------------------------------------------------- API schemas


class LeadOut(BaseModel):
    id: uuid.UUID
    email: str
    first_name: str | None
    last_name: str | None
    title: str | None
    seniority: str | None = None  # enrichment-fillable (M2.1)
    phone: str | None = None  # enrichment-fillable (M2.1)
    status: str
    icp_score: float | None
    email_verified: bool
    source: str | None
    # Score provenance (M1.3). NULL on leads scored before this was tracked, or never
    # scored — the UI says so instead of fabricating a breakdown.
    icp_cosine: float | None = None
    icp_rule: float | None = None
    icp_signal: float | None = None  # M2.3: NULL = no signals (2-way blend was used)
    icp_scored_at: datetime | None = None
    icp_scored_campaign_id: uuid.UUID | None = None
    icp_scored_campaign_name: str | None = None
    icp_matched_keyword: str | None = None  # derived from title at read time

    model_config = {"from_attributes": True}


class LeadEnrichmentOut(BaseModel):
    """One provenance row: which provider said what about a lead, and when (M2.1)."""

    field: str
    value: str
    source: str
    confidence: float
    fetched_at: datetime

    model_config = {"from_attributes": True}


class ReviewItemOut(BaseModel):
    """A review-queue item with enough context to act on it.

    `message_id` is what makes a `classification` item actionable (reclassify targets a
    message); `enrollment_id` is what makes a `copywriter` item actionable (retry/skip/kill).
    """

    id: uuid.UUID
    kind: str
    message_id: uuid.UUID | None
    enrollment_id: uuid.UUID | None
    payload: dict | None
    created_at: datetime | None
    lead_email: str | None = None
    lead_name: str | None = None
    campaign_name: str | None = None
    enrollment_state: str | None = None
    message_body: str | None = None
    message_subject: str | None = None


class CampaignCreate(BaseModel):
    name: str
    icp_description: str
    value_prop: str
    sender_persona: dict = Field(default_factory=dict)
    daily_cap: int = 50
    scheduling_url: str | None = None
    info_doc_url: str | None = None
    steps: list[int] = Field(
        default=[0, 3, 4],
        description="wait_days per step; first entry is days before opener (usually 0)",
    )


class CampaignOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    daily_cap: int
    icp_description: str | None = None
    value_prop: str | None = None
    scheduling_url: str | None = None
    info_doc_url: str | None = None
    autopilot_enabled: bool = False

    model_config = {"from_attributes": True}


class VariantCreate(BaseModel):
    step_order: int
    name: str
    skeleton: str
    slot_schema: dict | None = Field(
        default=None,
        description="Optional; derived from the skeleton's placeholders when omitted.",
    )


class VariantOut(BaseModel):
    id: uuid.UUID
    name: str | None
    alpha: float
    beta: float
    active: bool

    model_config = {"from_attributes": True}


class VariantUpdate(BaseModel):
    name: str | None = None
    active: bool | None = None
    skeleton: str | None = Field(
        default=None,
        description="Editable only while the arm has zero trials; clone a new variant otherwise.",
    )


class VariantDetailOut(VariantOut):
    skeleton: str
    slot_schema: dict
    trials: int


ChannelName = Literal["email", "linkedin_task", "call_task"]


class StepOut(BaseModel):
    id: uuid.UUID
    step_order: int
    wait_days: int
    channel: str = "email"
    skip_on_expire: bool = False
    variants: list[VariantDetailOut]

    model_config = {"from_attributes": True}


class StepCreate(BaseModel):
    wait_days: int = Field(ge=0)
    channel: ChannelName = "email"
    skip_on_expire: bool = False


class StepUpdate(BaseModel):
    wait_days: int | None = Field(default=None, ge=0)
    channel: ChannelName | None = Field(
        default=None,
        description="Frozen once the campaign has enrollments (enrollments index by step).",
    )
    skip_on_expire: bool | None = None


class CampaignDetailOut(CampaignOut):
    sender_persona: dict | None = None
    enrollments: int = Field(description="Enrollment count; >0 freezes the sequence structure.")
    steps: list[StepOut]


class DryRunRequest(BaseModel):
    n: int = Field(default=3, ge=1, le=10, description="Sample size; capped to bound LLM spend.")


class DryRunItemOut(BaseModel):
    id: uuid.UUID
    lead_email: str
    lead_name: str | None
    icp_score: float | None
    variant_name: str | None
    subject: str | None
    body: str | None
    validator_ok: bool | None
    validator_errors: list | None
    delivered: bool
    error: str | None

    model_config = {"from_attributes": True}


class DryRunOut(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    status: str
    requested_n: int
    error: str | None
    created_at: datetime | None
    finished_at: datetime | None
    items: list[DryRunItemOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class CampaignUpdate(BaseModel):
    """Partial update; status transitions stay on activate/pause."""

    name: str | None = None
    icp_description: str | None = None
    value_prop: str | None = None
    sender_persona: dict | None = None
    daily_cap: int | None = Field(default=None, ge=1)
    scheduling_url: str | None = None
    info_doc_url: str | None = None


class MailboxCreate(BaseModel):
    email: EmailStr
    smtp_host: str
    smtp_port: int = 587
    smtp_user: str
    smtp_password: str
    imap_host: str | None = None  # omit / empty for Mailpit-only sandboxes
    imap_port: int = 993
    imap_password: str | None = None
    dkim_selector: str | None = None  # optional; else the DKIM check probes common selectors
    daily_limit: int = 40


class MailboxOut(BaseModel):
    id: uuid.UUID
    email: str
    smtp_host: str | None = None
    smtp_port: int | None = None
    imap_host: str | None = None
    dkim_selector: str | None = None
    daily_limit: int
    sent_today: int
    warmup_stage: int
    health: str

    model_config = {"from_attributes": True}


class MailboxUpdate(BaseModel):
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_password: str | None = None
    dkim_selector: str | None = None
    daily_limit: int | None = None
    health: str | None = None
    clear_imap: bool = False  # drop IMAP so poller skips this box (Mailpit HTTP instead)


# ---------------------------------------------------------------- deliverability (M1.4)


class DnsRecordOut(BaseModel):
    status: str  # pass | missing | error
    record: str | None = None
    recommended: str | None = None


class DmarcOut(BaseModel):
    status: str
    policy: str | None = None
    record: str | None = None
    recommended: str | None = None


class DkimOut(BaseModel):
    status: str
    selector: str | None = None
    record: str | None = None


class WarmupStepOut(BaseModel):
    day: int
    stage: int
    cap: int


class WarmupOut(BaseModel):
    stage: int
    effective_cap: int
    daily_limit: int
    sent_today: int
    advances_per_day: int
    schedule: list[WarmupStepOut]


class DeliverabilityReport(BaseModel):
    mailbox_id: uuid.UUID
    email: str
    domain: str
    primary_domain_warning: bool
    spf: DnsRecordOut
    dmarc: DmarcOut
    dkim: DkimOut
    warmup: WarmupOut


# ---------------------------------------------------------------- deliverability suite (M5.3)


class BlocklistOut(BaseModel):
    zone: str
    status: str  # listed | clear | error ("couldn't check", never a listing)
    listed_ips: list[str] = []


class DomainStats7dOut(BaseModel):
    sends: int
    hard_bounces: int
    spam_bounces: int  # complaint proxy — see deliverability/health.py
    bounce_rate: float
    complaint_rate: float


class DomainHealthOut(BaseModel):
    domain: str
    score: int  # 0-100, formula in deliverability/health.py docstring
    components: dict[str, int]  # points deducted per factor; 0 = clean
    mailboxes: int
    paused_mailboxes: int
    spf: DnsRecordOut
    dmarc: DmarcOut
    dkim: DkimOut
    blocklists: list[BlocklistOut]
    stats_7d: DomainStats7dOut


class PlacementCreate(BaseModel):
    campaign_id: uuid.UUID
    # ≤10 operator-owned seed addresses; schema-enforced so oversized or
    # non-email payloads never reach the send path
    seed_emails: list[EmailStr] = Field(min_length=1, max_length=10)


class PlacementMarkRequest(BaseModel):
    marks: dict[str, Literal["inbox", "spam", "missing"]]  # seed_email → verdict


class PlacementResultOut(BaseModel):
    id: uuid.UUID
    seed_email: str
    verdict: str  # pending|inbox|spam|missing
    delivered: bool
    error: str | None = None
    marked_at: datetime | None = None

    model_config = {"from_attributes": True}


class PlacementRunOut(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    status: str  # running|complete|failed
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    results: list[PlacementResultOut] = []

    model_config = {"from_attributes": True}



class MessageOut(BaseModel):
    id: uuid.UUID
    direction: str
    subject: str | None
    body: str | None
    classification: str | None
    classification_confidence: float | None = None
    sent_at: datetime | None
    lead_email: str | None = None
    lead_name: str | None = None
    company_domain: str | None = None

    model_config = {"from_attributes": True}


class MeetingOut(BaseModel):
    id: uuid.UUID
    enrollment_id: uuid.UUID | None
    provider: str
    provider_event_id: str
    status: str
    start_at: datetime | None
    booked_at: datetime | None
    created_at: datetime
    lead_email: str | None = None
    lead_name: str | None = None
    campaign_name: str | None = None

    model_config = {"from_attributes": True}


class EscalationMatch(BaseModel):
    """AND-ed conditions; None = wildcard."""

    classifications: list[str] | None = None
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    max_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    keywords_any: list[str] | None = None


class EscalationActions(BaseModel):
    notify: bool = False
    urgent_notify: bool = False
    suppress: bool = False
    review_queue: bool = False
    block_draft: bool = False
    block_autopilot: bool = False


class EscalationRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    priority: int = Field(default=100, ge=0)
    enabled: bool = True
    match: EscalationMatch = Field(default_factory=EscalationMatch)
    actions: EscalationActions = Field(default_factory=EscalationActions)


class EscalationRuleOut(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID | None
    name: str
    priority: int
    enabled: bool
    match: dict
    actions: dict
    builtin: bool = False
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class ReplyDraftOut(BaseModel):
    id: uuid.UUID
    inbound_message_id: uuid.UUID
    enrollment_id: uuid.UUID | None
    skeleton_key: str | None
    body: str | None
    status: str
    auto_sent: bool
    detail: dict | None = None
    sent_message_id: uuid.UUID | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    # joined context for the inbox UI
    lead_email: str | None = None
    lead_name: str | None = None
    campaign_name: str | None = None
    inbound_subject: str | None = None
    inbound_body: str | None = None
    inbound_classification: str | None = None

    model_config = {"from_attributes": True}


class ArmPosterior(BaseModel):
    variant_id: uuid.UUID
    name: str | None
    step_order: int
    alpha: float
    beta: float
    active: bool
    trials: int
    posterior_mean: float


class ImportResult(BaseModel):
    imported: int
    deduped: int
    suppressed: int
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- lead sourcing (M2.2)


class SourceFilters(BaseModel):
    """Structured ICP filters. Each provider maps what it supports; unknown filters are
    passed through honestly (no silent drop) where the provider accepts free text."""

    titles: list[str] = Field(default_factory=list)
    seniorities: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    company_domains: list[str] = Field(default_factory=list)
    employee_ranges: list[str] = Field(default_factory=list)  # e.g. "51,200"


class SourceSearchRequest(BaseModel):
    provider: str
    icp_query: str = ""
    filters: SourceFilters = Field(default_factory=SourceFilters)
    limit: int = Field(default=25, ge=1, le=50)  # capped to bound provider spend


class SourcedLeadIn(BaseModel):
    """A candidate row a client sends back to import. Re-checked by the gate — the
    client is untrusted, so preview labels are not load-bearing for safety."""

    email: str
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    company_name: str | None = None
    company_domain: str | None = None
    linkedin_url: str | None = None


class SourcedCandidate(SourcedLeadIn):
    status: str  # new | duplicate | suppressed | invalid (from the gate, read-only)


class SourcedPreview(BaseModel):
    provider: str
    candidates: list[SourcedCandidate]
    new: int
    duplicate: int
    suppressed: int
    invalid: int


class SourceImportRequest(BaseModel):
    source: str  # provider name, stamped onto imported leads
    leads: list[SourcedLeadIn] = Field(max_length=50)


# ---------------------------------------------------------------- intent signals (M2.3)

SignalType = Literal["funding", "leadership_hire", "job_posting", "tech_stack_change"]
SignalAction = Literal["boost_score", "enroll", "notify"]


class SignalOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    type: str
    payload: dict | None
    observed_at: datetime
    source: str | None

    model_config = {"from_attributes": True}


class SignalRuleCreate(BaseModel):
    signal_type: SignalType
    action: SignalAction
    active: bool = True


class SignalRuleOut(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    signal_type: str
    action: str
    active: bool

    model_config = {"from_attributes": True}


class ScoringWeights(BaseModel):
    """The active ICP-score weights, so the dashboard explains a score truthfully instead
    of hardcoding numbers that a knob change would falsify (M2.3)."""

    cosine: float
    rule: float
    signal_cosine: float
    signal_rule: float
    signal: float


# ---------------------------------------------------------------- touch tasks (M3)


class TaskOut(BaseModel):
    """A human-touch task with enough context to act on it without leaving the card:
    who, why (brief highlights), the validated content, and where to do it."""

    id: uuid.UUID
    enrollment_id: uuid.UUID
    channel: str
    step_order: int
    status: str
    outcome: str | None
    payload: dict
    due_at: datetime
    overdue: bool = False
    created_at: datetime | None
    resolved_at: datetime | None
    # lead + campaign context
    lead_id: uuid.UUID | None = None
    lead_email: str | None = None
    lead_name: str | None = None
    lead_title: str | None = None
    linkedin_url: str | None = None
    phone: str | None = None
    company_name: str | None = None
    company_domain: str | None = None
    campaign_id: uuid.UUID | None = None
    campaign_name: str | None = None
    # grounded research highlights (trigger events + pain points from the brief)
    brief_highlights: list[str] = Field(default_factory=list)
    # call_task only: true when a Twilio dialer is fully configured (M3.3)
    dialer_available: bool = False


class TaskCompleteRequest(BaseModel):
    outcome: str | None = Field(
        default=None,
        description="Channel-specific: linkedin_task = sent; call_task = connected|voicemail|no_answer.",
    )


class TimelineItemOut(BaseModel):
    """One entry in the unified per-lead touch history (M3.1): email sends, replies,
    and human-touch task events, newest first."""

    kind: str  # email_sent | reply | task
    at: datetime
    channel: str = "email"
    title: str
    detail: str | None = None
    classification: str | None = None
    status: str | None = None
    outcome: str | None = None
    campaign_name: str | None = None


# ---------------------------------------------------------------- API keys


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1)
    scopes: list[Literal["read", "operate", "admin"]] = Field(min_length=1)


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    scopes: list[str]
    created_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyOut):
    """Returned exactly once, on creation: includes the plaintext token."""

    token: str


class DeadLetterOut(BaseModel):
    id: uuid.UUID
    task_name: str
    task_id: str | None = None
    exception: str | None = None
    enrollment_id: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------- users & SSO (M5.1b)


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    display_name: str | None = None
    role: Literal["owner", "operator", "viewer"]
    has_password: bool = False
    sso_linked: bool = False
    disabled_at: datetime | None = None
    last_login_at: datetime | None = None
    created_at: datetime | None = None


class UserCreate(BaseModel):
    email: EmailStr
    role: Literal["owner", "operator", "viewer"] = "viewer"
    display_name: str | None = None
    # optional initial password; omitted = SSO-only until an owner sets one
    password: str | None = Field(default=None, min_length=12)


class UserUpdate(BaseModel):
    role: Literal["owner", "operator", "viewer"] | None = None
    display_name: str | None = None
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=12)


class CredentialsIn(BaseModel):
    email: EmailStr
    password: str


class CredentialVerifyOut(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    role: Literal["owner", "operator", "viewer"]
    display_name: str | None = None


class SsoExchangeIn(BaseModel):
    code: str


class SsoStatusOut(BaseModel):
    enabled: bool


class OrgOut(BaseModel):
    """The caller's own org: identity, quotas (null = unlimited), and today's
    usage. Quotas are set by the INSTANCE operator (python -m craftsman.manage_org),
    not by the tenant — this view is read-only on purpose."""

    id: uuid.UUID
    name: str
    slug: str
    daily_send_cap: int | None = None
    sent_today: int = 0
    max_mailboxes: int | None = None
    mailbox_count: int = 0
    enrichment_daily_budget: int | None = None
    enrichment_calls_today: int = 0


# ---------------------------------------------------------------- webhooks out (M5.4)


def _known_event_types(mask: list[str]) -> list[str]:
    """Shared mask validator: only registry event types, de-duped, order kept.
    The registry (webhooks/events.py) is the single source of truth — a forged
    event type is a 422 at the API edge, exactly like at emit time."""
    from craftsman.webhooks.events import EVENT_TYPES

    unknown = [e for e in mask if e not in EVENT_TYPES]
    if unknown:
        raise ValueError(f"unknown event type(s): {unknown} — valid: {list(EVENT_TYPES)}")
    return list(dict.fromkeys(mask))


class WebhookEndpointCreate(BaseModel):
    url: str = Field(min_length=1)
    event_mask: list[str] = Field(min_length=1)

    @field_validator("event_mask")
    @classmethod
    def _mask_known(cls, v: list[str]) -> list[str]:
        return _known_event_types(v)


class WebhookEndpointUpdate(BaseModel):
    url: str | None = None
    event_mask: list[str] | None = Field(default=None, min_length=1)
    active: bool | None = None

    @field_validator("event_mask")
    @classmethod
    def _mask_known(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _known_event_types(v)


class WebhookEndpointOut(BaseModel):
    id: uuid.UUID
    url: str
    event_mask: list[str]
    active: bool
    secret_prefix: str  # never the secret — shown once at creation only
    created_at: datetime | None = None


class WebhookEndpointCreated(WebhookEndpointOut):
    """Returned exactly once, on creation: includes the plaintext secret."""

    secret: str


class WebhookDeliveryOut(BaseModel):
    id: uuid.UUID
    endpoint_id: uuid.UUID
    event_type: str
    payload: dict
    status: Literal["pending", "delivered", "failed"]
    attempts: int
    last_error: str | None = None
    created_at: datetime | None = None
    delivered_at: datetime | None = None

    model_config = {"from_attributes": True}
