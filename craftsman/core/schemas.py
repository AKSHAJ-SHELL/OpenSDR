"""Pydantic schemas: API I/O + the LLM-facing structured-output models."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

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


class ReplyClassification(BaseModel):
    label: Literal["interested", "objection", "not_now", "ooo", "unsubscribe", "bounce_or_auto"]
    ooo_return_date: date | None = None
    confidence: float = Field(ge=0.0, le=1.0)


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


class StepOut(BaseModel):
    id: uuid.UUID
    step_order: int
    wait_days: int
    variants: list[VariantDetailOut]

    model_config = {"from_attributes": True}


class StepCreate(BaseModel):
    wait_days: int = Field(ge=0)


class StepUpdate(BaseModel):
    wait_days: int = Field(ge=0)


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
