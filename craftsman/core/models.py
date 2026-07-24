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
    # Enrichment-fillable facts (M2.1) — written only when empty, provenance in
    # lead_enrichments. `size` is text: providers report counts OR ranges ("51-200").
    industry: Mapped[str | None] = mapped_column(Text)
    size: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
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
    seniority: Mapped[str | None] = mapped_column(Text)  # enrichment-fillable (M2.1)
    phone: Mapped[str | None] = mapped_column(Text)  # enrichment-fillable (M2.1)
    linkedin_url: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, default="America/Los_Angeles")
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    icp_score: Mapped[float | None] = mapped_column(Float)
    # Score provenance (M1.3): a lead is re-scored by every campaign activation, so the
    # bare score is meaningless without knowing which ICP produced it and from what parts.
    icp_cosine: Mapped[float | None] = mapped_column(Float)
    icp_rule: Mapped[float | None] = mapped_column(Float)
    # Signal component (M2.3): NULL means the company had no signals at scoring time, so
    # the 2-way (cosine/rule) blend was used; a value means the 3-way blend was used.
    icp_signal: Mapped[float | None] = mapped_column(Float)
    icp_scored_campaign_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("campaigns.id"))
    icp_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, default="new")  # new|verified|disqualified|suppressed
    source: Mapped[str | None] = mapped_column(Text)  # csv|apollo|hunter
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    company: Mapped[Company | None] = relationship(back_populates="leads")


class LeadEnrichmentRecord(Base):
    """Append-only provenance: which provider said what about a lead, and when.

    One row per field the chain resolved — including fields whose canonical column
    already held operator data (the audit trail shows the disagreement). Values are
    prospect PII: `erase_lead` deletes these rows explicitly. Per the M0.4 decision
    the FK does NOT cascade — accidental lead deletion stays blocked by constraint.
    """

    __tablename__ = "lead_enrichments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id"), nullable=False, index=True
    )
    field: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # apollo|hunter|...
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Signal(Base):
    """An intent observation about a company (M2.3): funding, hiring, tech-stack moves.
    Company-level, not person PII — so erasure of a lead does not touch these. Feeds a
    decaying signal component of the ICP score and can trigger signal_rules."""

    __tablename__ = "signals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)  # funding|leadership_hire|job_posting|tech_stack_change
    payload: Mapped[dict | None] = mapped_column(JSONB)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    source: Mapped[str | None] = mapped_column(Text)  # collector name / feed url

    __table_args__ = (Index("ix_signals_company_type_observed", "company_id", "type", "observed_at"),)


class SignalRule(Base):
    """Per-campaign policy: when a signal_type is observed, do `action`. `enroll` is the
    only autonomy-bearing action and fires only where an operator created the rule (M2.3)."""

    __tablename__ = "signal_rules"

    id: Mapped[uuid.UUID] = _uuid_pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    signal_type: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)  # boost_score|enroll|notify
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))

    __table_args__ = (
        UniqueConstraint("campaign_id", "signal_type", "action", name="uq_signal_rule"),
    )


class CollectorState(Base):
    """Per-company, per-collector fingerprint so diff-based collectors detect *change*
    (careers/homepage) and dedupe feed entries (funding) without re-emitting (M2.3)."""

    __tablename__ = "collector_state"

    id: Mapped[uuid.UUID] = _uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    collector: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (UniqueConstraint("company_id", "collector", name="uq_collector_state"),)


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
    # M4.3: static links embedded in reply drafts — campaign config, never LLM output.
    # scheduling_url: the rep's booking page (Cal.com etc.); info_doc_url: the approved
    # one-pager for "send me info" replies. Empty = the corresponding line is omitted.
    scheduling_url: Mapped[str | None] = mapped_column(Text)
    info_doc_url: Mapped[str | None] = mapped_column(Text)
    # M4.4 (⛔ Gate M4 Option B): opt-in Guarded Autopilot. Enable requires an
    # admin-scoped call; the operate-scoped kill switch disables instantly.
    autopilot_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

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
    # M3.1: email | linkedin_task | call_task (craftsman/channels.py). Email is the only
    # autonomous channel; task channels queue validated content for a human.
    channel: Mapped[str] = mapped_column(
        Text, nullable=False, default="email", server_default=text("'email'")
    )
    # M3.1 (⛔ Gate M3 decision): default false = an undone task HOLDS the sequence
    # (surfaced as overdue); true = expiry marks the task expired and advances.
    skip_on_expire: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

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

    @property
    def trials(self) -> int:
        """Observed trials under the Beta(1,1) prior; editing a tried arm rewrites history."""
        return int(self.alpha + self.beta - 2)


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("lead_id", "campaign_id"),
        Index(
            "idx_enroll_due",
            "next_action_at",
            postgresql_where=text(
                "state IN ('queued','ready','waiting','ooo_rescheduled','awaiting_human_touch')"
            ),
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )  # when the row (incl. an outbound claim) was created — drives the unsent-claim sweep

    enrollment: Mapped[Enrollment | None] = relationship()
    variant: Mapped[Variant | None] = relationship()


class TouchTask(Base):
    """A human-touch task for an assisted-channel step (M3.1): the validated message
    (LinkedIn) or grounded call brief, waiting for a human to perform the touch.

    Idempotency mirrors the email send claim: UNIQUE(enrollment_id, step_order) makes a
    duplicate generate_touch_task insert trip IntegrityError and skip. `payload` holds
    lead-personalized content (person PII) — `erase_lead` deletes these rows explicitly.
    Per M0.4 doctrine the FK does NOT cascade.
    """

    __tablename__ = "touch_tasks"
    __table_args__ = (
        UniqueConstraint("enrollment_id", "step_order", name="uq_touch_task_step"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("enrollments.id"), nullable=False, index=True
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)  # linkedin_task|call_task
    variant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("variants.id"))
    # linkedin_task: {"message": str, "char_count": int, "slots": {...}}
    # call_task:     {"brief": {"opener": ..., "pain_hypotheses": [...], "objection_notes": ...}}
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="open", server_default=text("'open'")
    )  # open|done|skipped|expired|cancelled
    outcome: Mapped[str | None] = mapped_column(Text)  # sent | connected|voicemail|no_answer
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    enrollment: Mapped[Enrollment] = relationship()


class Meeting(Base):
    """A booked meeting (M4.3, G8) — the funnel's terminal win, learned from a
    signed calendar-provider webhook. UNIQUE(provider_event_id) makes webhook
    redelivery an update, not a duplicate. No FK cascade per M0.4 doctrine;
    erase_lead deletes these rows (attendee identity is person-linked)."""

    __tablename__ = "meetings"
    __table_args__ = (
        UniqueConstraint("provider_event_id", name="uq_meeting_provider_event"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("enrollments.id"), index=True
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # proposed|booked|completed|cancelled|no_show
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    enrollment: Mapped[Enrollment | None] = relationship()


class EscalationRule(Base):
    """When a human is pulled in, as data (M4.2, G9). NULL campaign_id = global.

    `match` JSONB: {classifications: [..]|null, min_confidence, max_confidence,
    keywords_any: [..]|null} — AND-ed, null = wildcard. `actions` JSONB: {notify,
    urgent_notify, suppress, review_queue, block_draft, block_autopilot} booleans.
    DB rules ADD to the built-in defaults (inbox/escalation.py) — the legal-threat
    tripwire and the interested-notify baseline cannot be disabled from data.
    """

    __tablename__ = "escalation_rules"

    id: Mapped[uuid.UUID] = _uuid_pk()
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id"), index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default=text("100")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    match: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    actions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ReplyDraft(Base):
    """A validated, skeleton-rendered reply waiting for a human (M4.1 Copilot).

    Exactly one draft per inbound message (UNIQUE) — the insert is the generation
    idempotency claim, mirroring the send/touch-task pattern. Statuses:
      generating   — claim row, LLM round-trip in flight
      pending      — validated draft awaiting a human decision
      sending      — dispatch claim (CAS from pending) held across the SMTP send
      sent         — approved verbatim and dispatched (human click, or Autopilot
                     with auto_sent=true under M4.4's policy engine)
      edited_sent  — human edited, edit re-validated, dispatched
      discarded    — human declined (or suppression cancelled it; see detail)
      failed       — validator rejected twice; routed to review queue
      skipped      — deliberately not drafted (objection kind needs a human,
                     escalation blocked it) — recorded for auditability
    `body` is lead-personalized content quoting prospect-authored text (person PII):
    erase_lead deletes these rows, before messages (FK). No FK cascade per M0.4.
    """

    __tablename__ = "reply_drafts"
    __table_args__ = (
        UniqueConstraint("inbound_message_id", name="uq_reply_draft_inbound"),
        # F-05 (findings/12): the ≤1-auto-reply-per-thread invariant, structural.
        # The auto dispatch path stamps auto_sent at its CAS claim — before any
        # I/O — so a second qualifying draft in the same thread trips this index
        # instead of reaching SMTP, closing the read-then-act race between
        # parallel workers. Released (pending/discarded) drafts clear auto_sent
        # and free the reservation.
        Index(
            "uq_auto_reply_per_thread",
            "enrollment_id",
            unique=True,
            postgresql_where=text("auto_sent AND enrollment_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    inbound_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id"), nullable=False
    )
    enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("enrollments.id"), index=True
    )
    skeleton_key: Mapped[str | None] = mapped_column(Text)  # reply_interested | reply_objection_timing | reply_objection_info
    slots: Mapped[dict | None] = mapped_column(JSONB)
    body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default=text("'pending'")
    )
    auto_sent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    detail: Mapped[dict | None] = mapped_column(JSONB)  # validator errors / skip reason
    sent_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("messages.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    inbound_message: Mapped[Message] = relationship(foreign_keys=[inbound_message_id])
    enrollment: Mapped[Enrollment | None] = relationship()


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
    dkim_selector: Mapped[str | None] = mapped_column(Text)  # authoritative for the DKIM check; else probed
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


class DeadLetter(Base):
    """A Celery task that failed terminally (retries exhausted). Redis has no native
    dead-letter queue, so we record failures here for inspection and manual re-drive."""

    __tablename__ = "dead_letters"

    id: Mapped[uuid.UUID] = _uuid_pk()
    task_name: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str | None] = mapped_column(Text)
    args: Mapped[list | None] = mapped_column(JSONB)
    kwargs: Mapped[dict | None] = mapped_column(JSONB)
    exception: Mapped[str | None] = mapped_column(Text)
    traceback: Mapped[str | None] = mapped_column(Text)
    # the enrollment the task referenced, if any (plain id — no FK, so erasure and
    # enrollment deletion never trip over dead-letter rows)
    enrollment_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class DryRun(Base):
    """A preflight run: the real pipeline for N sample leads, delivered to Mailpit only.
    Touches none of the production state (enrollments, messages, caps, bandit)."""

    __tablename__ = "dry_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")  # running|complete|failed
    requested_n: Mapped[int] = mapped_column(Integer, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["DryRunItem"]] = relationship(back_populates="dry_run")


class DryRunItem(Base):
    """One sampled lead's trip through the dry-run pipeline. Holds lead PII (email,
    personalized copy), so erase_lead deletes these rows by lead_id."""

    __tablename__ = "dry_run_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    dry_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dry_runs.id"), nullable=False)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.id"), index=True)
    lead_email: Mapped[str] = mapped_column(Text, nullable=False)
    lead_name: Mapped[str | None] = mapped_column(Text)
    icp_score: Mapped[float | None] = mapped_column(Float)
    variant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("variants.id"))
    variant_name: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    validator_ok: Mapped[bool | None] = mapped_column(Boolean)
    validator_errors: Mapped[list | None] = mapped_column(JSONB)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)

    dry_run: Mapped[DryRun] = relationship(back_populates="items")


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
