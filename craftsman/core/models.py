"""SQLAlchemy models — mirrors the design doc schema exactly."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 1024


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    domain: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    research_brief: Mapped[dict | None] = mapped_column(JSONB)
    research_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedding: Mapped[list | None] = mapped_column(Vector(EMBEDDING_DIM))

    leads: Mapped[list["Lead"]] = relationship(back_populates="company")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id"))
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    linkedin_url: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, default="America/Los_Angeles")
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    icp_score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(Text, default="new")  # new|verified|disqualified|suppressed
    source: Mapped[str | None] = mapped_column(Text)  # csv|apollo|hunter
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    company: Mapped[Company | None] = relationship(back_populates="leads")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    icp_description: Mapped[str] = mapped_column(Text, nullable=False)
    value_prop: Mapped[str] = mapped_column(Text, nullable=False)
    sender_persona: Mapped[dict | None] = mapped_column(JSONB)
    daily_cap: Mapped[int] = mapped_column(Integer, default=50)
    # atomic per-day send counter (reserve/release); reset daily. The cap gate reads
    # THIS, not a message count, so concurrent workers can't collectively over-send.
    sent_today: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(Text, default="draft")  # draft|active|paused|done
    icp_embedding: Mapped[list | None] = mapped_column(Vector(EMBEDDING_DIM))

    steps: Mapped[list["SequenceStep"]] = relationship(
        back_populates="campaign", order_by="SequenceStep.step_order"
    )


class SequenceStep(Base):
    __tablename__ = "sequence_steps"
    __table_args__ = (UniqueConstraint("campaign_id", "step_order"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)  # 1=opener, 2=bump, 3=breakup
    wait_days: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    campaign: Mapped[Campaign] = relationship(back_populates="steps")
    variants: Mapped[list["Variant"]] = relationship(back_populates="step")


class Variant(Base):
    __tablename__ = "variants"

    id: Mapped[uuid.UUID] = _uuid_pk()
    step_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sequence_steps.id"))
    name: Mapped[str | None] = mapped_column(Text)  # pain_led | trigger_led | question_led
    skeleton: Mapped[str] = mapped_column(Text, nullable=False)
    slot_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    alpha: Mapped[float] = mapped_column(Float, default=1.0)  # Beta prior: successes + 1
    beta: Mapped[float] = mapped_column(Float, default=1.0)  # Beta prior: failures + 1
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    step: Mapped[SequenceStep] = relationship(back_populates="variants")


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("lead_id", "campaign_id"),
        Index(
            "idx_enroll_due",
            "next_action_at",
            postgresql_where=text("state IN ('queued','ready','waiting','ooo_rescheduled')"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id"))
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    state: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lead: Mapped[Lead] = relationship()
    campaign: Mapped[Campaign] = relationship()


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        # Idempotency: at most one outbound message per (enrollment, step). The send
        # path claims this row BEFORE delivering, so an acks_late retry hits the
        # constraint and skips rather than sending a duplicate.
        Index(
            "uq_outbound_step",
            "enrollment_id",
            "step_order",
            unique=True,
            postgresql_where=text("direction = 'outbound'"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    enrollment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("enrollments.id"))
    variant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("variants.id"))
    direction: Mapped[str] = mapped_column(Text, nullable=False)  # outbound|inbound
    step_order: Mapped[int | None] = mapped_column(Integer)  # sequence step, outbound only
    mailbox_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mailboxes.id"))
    subject: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    smtp_message_id: Mapped[str | None] = mapped_column(Text)
    classification: Mapped[str | None] = mapped_column(Text)  # inbound only
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    bandit_outcome: Mapped[str | None] = mapped_column(Text)  # pending|success|failure
    outcome_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    enrollment: Mapped[Enrollment | None] = relationship()
    variant: Mapped[Variant | None] = relationship()


class Mailbox(Base):
    __tablename__ = "mailboxes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    smtp_host: Mapped[str | None] = mapped_column(Text)
    smtp_port: Mapped[int | None] = mapped_column(Integer)
    smtp_user: Mapped[str | None] = mapped_column(Text)
    smtp_pass_enc: Mapped[str | None] = mapped_column(Text)
    imap_host: Mapped[str | None] = mapped_column(Text)
    imap_port: Mapped[int | None] = mapped_column(Integer)
    imap_pass_enc: Mapped[str | None] = mapped_column(Text)
    daily_limit: Mapped[int] = mapped_column(Integer, default=40)
    sent_today: Mapped[int] = mapped_column(Integer, default=0)
    warmup_stage: Mapped[int] = mapped_column(Integer, default=0)  # 0..4; caps ramp 10→20→30→40
    health: Mapped[str] = mapped_column(Text, default="ok")  # ok|degraded|paused
    hard_bounces_today: Mapped[int] = mapped_column(Integer, default=0)


class SuppressionEntry(Base):
    __tablename__ = "suppression_list"

    email: Mapped[str] = mapped_column(Text, primary_key=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)  # unsubscribe|bounce|manual|gdpr
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    enrollment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("enrollments.id"))
    from_state: Mapped[str | None] = mapped_column(Text)
    to_state: Mapped[str | None] = mapped_column(Text)
    event: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ReviewQueueItem(Base):
    """Low-confidence classifications and validator failures land here for a human."""

    __tablename__ = "review_queue"

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # classification|copywriter
    message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id"))
    enrollment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("enrollments.id"))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class UnsubscribeToken(Base):
    __tablename__ = "unsubscribe_tokens"

    token: Mapped[str] = mapped_column(Text, primary_key=True)
    lead_email: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ApiKey(Base):
    """A hashed, scoped API key. The plaintext token is shown once at creation;
    only its SHA-256 digest is stored here."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(Text, nullable=False)  # first chars, for UI/logs
    key_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # read|operate|admin
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
