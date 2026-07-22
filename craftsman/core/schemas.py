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


class LeadEnrichment(BaseModel):
    """Common output shape for enrichment adapters (Apollo, Hunter, ...)."""

    email: EmailStr | None = None
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    linkedin_url: str | None = None
    company_name: str | None = None
    company_domain: str | None = None
    company_description: str | None = None


# ---------------------------------------------------------------- API schemas


class LeadOut(BaseModel):
    id: uuid.UUID
    email: str
    first_name: str | None
    last_name: str | None
    title: str | None
    status: str
    icp_score: float | None
    email_verified: bool
    source: str | None

    model_config = {"from_attributes": True}


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
    steps: list[StepOut]


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
    daily_limit: int = 40


class MailboxOut(BaseModel):
    id: uuid.UUID
    email: str
    smtp_host: str | None = None
    smtp_port: int | None = None
    imap_host: str | None = None
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
    daily_limit: int | None = None
    health: str | None = None
    clear_imap: bool = False  # drop IMAP so poller skips this box (Mailpit HTTP instead)



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
