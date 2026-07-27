import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.copywriter.fill import skeleton_slots
from craftsman.core.config import get_settings
from craftsman.core.models import (
    Campaign,
    DryRun,
    Enrollment,
    EscalationRule,
    Lead,
    SequenceStep,
    SignalRule,
    Variant,
)
from craftsman.core.schemas import (
    ArmPosterior,
    CampaignCreate,
    CampaignDetailOut,
    CampaignOut,
    CampaignUpdate,
    DryRunItemOut,
    DryRunOut,
    DryRunRequest,
    EscalationRuleCreate,
    EscalationRuleOut,
    SignalRuleCreate,
    SignalRuleOut,
    StepCreate,
    StepOut,
    StepUpdate,
    VariantCreate,
    VariantDetailOut,
    VariantUpdate,
)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _get_campaign_or_404(db: Session, campaign_id: uuid.UUID) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")
    return campaign


def _has_enrollments(db: Session, campaign_id: uuid.UUID) -> bool:
    return db.scalar(select(Enrollment.id).where(Enrollment.campaign_id == campaign_id).limit(1)) is not None


def _checked_slot_schema(skeleton: str, channel: str = "email") -> dict:
    """422 on placeholders outside the channel's slot vocabulary; derive slot_schema
    from the rest.

    render_skeleton raises on unfilled placeholders at send time — this moves that
    failure to authoring time, where the author can fix it. Vocabulary is per channel
    (M3.1): email and linkedin_task have different slot sets; call_task has none
    (structured brief, no skeleton).
    """
    from craftsman.channels import get_channel

    spec = get_channel(channel)
    if not spec.uses_skeleton:
        raise HTTPException(
            422, f"channel '{channel}' uses a structured brief, not skeleton variants"
        )
    known = spec.llm_slots | spec.static_slots
    unknown = skeleton_slots(skeleton) - known
    if unknown:
        raise HTTPException(
            422,
            f"unknown skeleton placeholder(s) for channel '{channel}': {sorted(unknown)}; "
            f"valid slots are {sorted(known)}",
        )
    return {slot: "string" for slot in sorted(skeleton_slots(skeleton) & spec.llm_slots)}


def _campaign_detail(db: Session, campaign: Campaign) -> CampaignDetailOut:
    steps = db.scalars(
        select(SequenceStep)
        .where(SequenceStep.campaign_id == campaign.id)
        .order_by(SequenceStep.step_order)
    ).all()
    enrollments = db.scalar(
        select(func.count(Enrollment.id)).where(Enrollment.campaign_id == campaign.id)
    )
    return CampaignDetailOut(
        id=campaign.id,
        name=campaign.name,
        status=campaign.status,
        daily_cap=campaign.daily_cap,
        icp_description=campaign.icp_description,
        value_prop=campaign.value_prop,
        sender_persona=campaign.sender_persona,
        enrollments=enrollments or 0,
        steps=[
            StepOut(
                id=s.id,
                step_order=s.step_order,
                wait_days=s.wait_days,
                channel=s.channel,
                skip_on_expire=s.skip_on_expire,
                variants=[
                    VariantDetailOut.model_validate(v)
                    for v in sorted(s.variants, key=lambda v: (v.name or "", v.id.hex))
                ],
            )
            for s in steps
        ],
    )


@router.get("", response_model=list[CampaignOut], dependencies=[Depends(require_scope("read"))])
def list_campaigns(db: Session = Depends(get_db)):
    return list(db.scalars(select(Campaign).order_by(Campaign.name)).all())


@router.get(
    "/{campaign_id}", response_model=CampaignDetailOut, dependencies=[Depends(require_scope("read"))]
)
def get_campaign(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    """Full read-back for the builder UI: campaign + persona + steps + variants."""
    return _campaign_detail(db, _get_campaign_or_404(db, campaign_id))


@router.patch(
    "/{campaign_id}", response_model=CampaignDetailOut, dependencies=[Depends(require_scope("operate"))]
)
async def update_campaign(
    campaign_id: uuid.UUID, payload: CampaignUpdate, db: Session = Depends(get_db)
):
    campaign = _get_campaign_or_404(db, campaign_id)
    changes = payload.model_dump(exclude_unset=True)
    reembed = "icp_description" in changes and changes["icp_description"] != campaign.icp_description
    for field, value in changes.items():
        setattr(campaign, field, value)
    if reembed:
        from craftsman.scoring.embeddings import get_embedder

        campaign.icp_embedding = (await get_embedder().embed([campaign.icp_description]))[0]
    db.add(campaign)
    return _campaign_detail(db, campaign)


@router.post("", response_model=CampaignOut, dependencies=[Depends(require_scope("operate"))])
async def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db)):
    from craftsman.scoring.embeddings import get_embedder

    campaign = Campaign(
        name=payload.name,
        icp_description=payload.icp_description,
        value_prop=payload.value_prop,
        sender_persona=payload.sender_persona,
        daily_cap=payload.daily_cap,
        scheduling_url=payload.scheduling_url,
        info_doc_url=payload.info_doc_url,
    )
    embedder = get_embedder()
    campaign.icp_embedding = (await embedder.embed([payload.icp_description]))[0]
    db.add(campaign)
    db.flush()
    for i, wait_days in enumerate(payload.steps, start=1):
        db.add(SequenceStep(campaign_id=campaign.id, step_order=i, wait_days=wait_days))
    return campaign


@router.post(
    "/{campaign_id}/steps",
    response_model=StepOut,
    status_code=201,
    dependencies=[Depends(require_scope("operate"))],
)
def add_step(campaign_id: uuid.UUID, payload: StepCreate, db: Session = Depends(get_db)):
    """Append a step. Structural changes are blocked once anyone is enrolled —
    enrollments index into the sequence by current_step."""
    _get_campaign_or_404(db, campaign_id)
    if _has_enrollments(db, campaign_id):
        raise HTTPException(409, "campaign has enrollments; sequence structure is frozen")
    last = db.scalar(
        select(SequenceStep.step_order)
        .where(SequenceStep.campaign_id == campaign_id)
        .order_by(SequenceStep.step_order.desc())
        .limit(1)
    )
    step = SequenceStep(
        campaign_id=campaign_id,
        step_order=(last or 0) + 1,
        wait_days=payload.wait_days,
        channel=payload.channel,
        skip_on_expire=payload.skip_on_expire,
    )
    db.add(step)
    db.flush()
    return StepOut(
        id=step.id, step_order=step.step_order, wait_days=step.wait_days,
        channel=step.channel, skip_on_expire=step.skip_on_expire, variants=[],
    )


@router.patch(
    "/{campaign_id}/steps/{step_id}",
    response_model=StepOut,
    dependencies=[Depends(require_scope("operate"))],
)
def update_step(
    campaign_id: uuid.UUID, step_id: uuid.UUID, payload: StepUpdate, db: Session = Depends(get_db)
):
    """wait_days/skip_on_expire are read at scheduling time — safe to change live.
    channel is structural (variants + generated content are per-channel), so it is
    frozen once anyone is enrolled, like add/delete step."""
    step = db.scalar(
        select(SequenceStep).where(
            SequenceStep.id == step_id, SequenceStep.campaign_id == campaign_id
        )
    )
    if step is None:
        raise HTTPException(404, "step not found in campaign")
    changes = payload.model_dump(exclude_unset=True)
    if "channel" in changes and changes["channel"] != step.channel:
        if _has_enrollments(db, campaign_id):
            raise HTTPException(409, "campaign has enrollments; step channel is frozen")
        if step.variants:
            # skeletons are written in a channel's slot vocabulary — they don't port
            raise HTTPException(
                409, "step has variants; delete them before changing the channel"
            )
        step.channel = changes["channel"]
    if changes.get("wait_days") is not None:
        step.wait_days = changes["wait_days"]
    if changes.get("skip_on_expire") is not None:
        step.skip_on_expire = changes["skip_on_expire"]
    db.add(step)
    return StepOut(
        id=step.id,
        step_order=step.step_order,
        wait_days=step.wait_days,
        channel=step.channel,
        skip_on_expire=step.skip_on_expire,
        variants=[VariantDetailOut.model_validate(v) for v in step.variants],
    )


@router.delete(
    "/{campaign_id}/steps/{step_id}",
    status_code=204,
    dependencies=[Depends(require_scope("operate"))],
)
def delete_step(campaign_id: uuid.UUID, step_id: uuid.UUID, db: Session = Depends(get_db)):
    _get_campaign_or_404(db, campaign_id)
    if _has_enrollments(db, campaign_id):
        raise HTTPException(409, "campaign has enrollments; sequence structure is frozen")
    step = db.scalar(
        select(SequenceStep).where(
            SequenceStep.id == step_id, SequenceStep.campaign_id == campaign_id
        )
    )
    if step is None:
        raise HTTPException(404, "step not found in campaign")
    for variant in step.variants:
        db.delete(variant)
    db.delete(step)
    db.flush()
    # Renumber ascending: each row moves into the slot just freed, so the
    # (campaign_id, step_order) unique constraint holds at every flush.
    followers = db.scalars(
        select(SequenceStep)
        .where(SequenceStep.campaign_id == campaign_id, SequenceStep.step_order > step.step_order)
        .order_by(SequenceStep.step_order)
    ).all()
    for follower in followers:
        follower.step_order -= 1
        db.add(follower)
        db.flush()


@router.post(
    "/{campaign_id}/variants",
    response_model=VariantDetailOut,
    dependencies=[Depends(require_scope("operate"))],
)
def add_variant(campaign_id: uuid.UUID, payload: VariantCreate, db: Session = Depends(get_db)):
    step = db.scalar(
        select(SequenceStep).where(
            SequenceStep.campaign_id == campaign_id,
            SequenceStep.step_order == payload.step_order,
        )
    )
    if step is None:
        raise HTTPException(404, f"no step {payload.step_order} in campaign")
    slot_schema = _checked_slot_schema(payload.skeleton, step.channel)
    variant = Variant(
        step_id=step.id, name=payload.name,
        skeleton=payload.skeleton,
        slot_schema=payload.slot_schema if payload.slot_schema is not None else slot_schema,
    )
    db.add(variant)
    db.flush()
    return variant


@router.patch(
    "/{campaign_id}/variants/{variant_id}",
    response_model=VariantDetailOut,
    dependencies=[Depends(require_scope("operate"))],
)
def update_variant(
    campaign_id: uuid.UUID,
    variant_id: uuid.UUID,
    payload: VariantUpdate,
    db: Session = Depends(get_db),
):
    variant = db.scalar(
        select(Variant)
        .join(SequenceStep, Variant.step_id == SequenceStep.id)
        .where(Variant.id == variant_id, SequenceStep.campaign_id == campaign_id)
    )
    if variant is None:
        raise HTTPException(404, "variant not found in campaign")
    changes = payload.model_dump(exclude_unset=True)
    if "skeleton" in changes and changes["skeleton"] != variant.skeleton:
        # The posterior measured replies to this exact skeleton; rewriting it would
        # attach measured history to copy that was never sent.
        if variant.trials > 0:
            raise HTTPException(
                409,
                f"variant has {variant.trials} recorded trial(s); its skeleton is frozen — "
                "clone it as a new variant (fresh arm) and deactivate this one instead",
            )
        step = db.get(SequenceStep, variant.step_id)
        variant.slot_schema = _checked_slot_schema(changes["skeleton"], step.channel)
        variant.skeleton = changes["skeleton"]
    if "name" in changes:
        variant.name = changes["name"]
    if "active" in changes:
        variant.active = changes["active"]
    db.add(variant)
    return variant


@router.post(
    "/{campaign_id}/activate",
    response_model=CampaignOut,
    dependencies=[Depends(require_scope("operate"))],
)
async def activate(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    """Activate: score all verified leads against the ICP and enroll those above threshold."""

    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")
    steps = db.scalars(select(SequenceStep).where(SequenceStep.campaign_id == campaign_id)).all()
    if not steps:
        raise HTTPException(400, "campaign has no steps")
    # skeleton-based steps (email, linkedin_task) need ≥1 variant somewhere to have
    # anything to generate; call_task steps use a structured brief and need none (M3.1)
    from craftsman.channels import get_channel

    skeleton_steps = [s for s in steps if get_channel(s.channel).uses_skeleton]
    has_variants = any(
        db.scalar(select(Variant.id).where(Variant.step_id == s.id).limit(1))
        for s in skeleton_steps
    )
    if skeleton_steps and not has_variants:
        raise HTTPException(400, "campaign has no variants; add at least one per step")

    from craftsman.scoring.enroll import score_and_enroll

    leads = db.scalars(
        select(Lead).where(Lead.email_verified.is_(True), Lead.status == "verified")
    ).all()
    await score_and_enroll(db, campaign, leads)

    campaign.status = "active"
    db.add(campaign)
    return campaign


@router.post(
    "/{campaign_id}/pause",
    response_model=CampaignOut,
    dependencies=[Depends(require_scope("operate"))],
)
def pause(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")
    campaign.status = "paused"
    db.add(campaign)
    return campaign


# ---------------------------------------------------------------- signal rules (M2.3)


@router.get(
    "/{campaign_id}/signal-rules",
    response_model=list[SignalRuleOut],
    dependencies=[Depends(require_scope("read"))],
)
def list_signal_rules(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.scalars(
        select(SignalRule).where(SignalRule.campaign_id == campaign_id)
    ).all()


@router.post(
    "/{campaign_id}/signal-rules",
    response_model=SignalRuleOut,
    status_code=201,
    dependencies=[Depends(require_scope("operate"))],
)
def create_signal_rule(
    campaign_id: uuid.UUID, body: SignalRuleCreate, db: Session = Depends(get_db)
):
    """Wire a signal to an action for this campaign. `enroll` is deliberate autonomy:
    creating it means a matching signal may auto-enroll verified, above-threshold leads
    (into `queued` — research/validation still run). Off until you create it."""
    if db.get(Campaign, campaign_id) is None:
        raise HTTPException(404, "campaign not found")
    existing = db.scalar(
        select(SignalRule).where(
            SignalRule.campaign_id == campaign_id,
            SignalRule.signal_type == body.signal_type,
            SignalRule.action == body.action,
        )
    )
    if existing is not None:
        raise HTTPException(409, "rule already exists for this signal_type + action")
    rule = SignalRule(
        campaign_id=campaign_id,
        signal_type=body.signal_type,
        action=body.action,
        active=body.active,
    )
    db.add(rule)
    db.flush()
    return rule


@router.delete(
    "/{campaign_id}/signal-rules/{rule_id}",
    status_code=204,
    dependencies=[Depends(require_scope("operate"))],
)
def delete_signal_rule(
    campaign_id: uuid.UUID, rule_id: uuid.UUID, db: Session = Depends(get_db)
):
    rule = db.get(SignalRule, rule_id)
    if rule is None or rule.campaign_id != campaign_id:
        raise HTTPException(404, "signal rule not found")
    db.delete(rule)


@router.post(
    "/{campaign_id}/dry-run",
    response_model=DryRunOut,
    status_code=202,
    dependencies=[Depends(require_scope("operate"))],
)
def start_dry_run(
    campaign_id: uuid.UUID, payload: DryRunRequest, db: Session = Depends(get_db)
):
    """Preflight the real pipeline for N sample leads; sends go to Mailpit only."""
    campaign = _get_campaign_or_404(db, campaign_id)
    step = db.scalar(
        select(SequenceStep).where(
            SequenceStep.campaign_id == campaign.id, SequenceStep.step_order == 1
        )
    )
    has_variants = step is not None and db.scalar(
        select(Variant.id).where(Variant.step_id == step.id, Variant.active).limit(1)
    )
    if not has_variants:
        raise HTTPException(400, "campaign has no active variants on step 1; add one first")

    run = DryRun(campaign_id=campaign.id, status="running", requested_n=payload.n)
    db.add(run)
    db.commit()  # durable before the worker looks for it

    from craftsman.workers.tasks import run_dry_run

    run_dry_run.delay(str(run.id))
    return DryRunOut.model_validate(run)


@router.get(
    "/{campaign_id}/dry-runs",
    response_model=list[DryRunOut],
    dependencies=[Depends(require_scope("read"))],
)
def list_dry_runs(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    _get_campaign_or_404(db, campaign_id)
    runs = db.scalars(
        select(DryRun)
        .where(DryRun.campaign_id == campaign_id)
        .order_by(DryRun.created_at.desc())
        .limit(10)
    ).all()
    return [_dry_run_out(run) for run in runs]


@router.get(
    "/{campaign_id}/dry-runs/{run_id}",
    response_model=DryRunOut,
    dependencies=[Depends(require_scope("read"))],
)
def get_dry_run(campaign_id: uuid.UUID, run_id: uuid.UUID, db: Session = Depends(get_db)):
    run = db.scalar(
        select(DryRun).where(DryRun.id == run_id, DryRun.campaign_id == campaign_id)
    )
    if run is None:
        raise HTTPException(404, "dry run not found in campaign")
    return _dry_run_out(run)


def _dry_run_out(run: DryRun) -> DryRunOut:
    out = DryRunOut.model_validate(run)
    out.items = sorted(
        (DryRunItemOut.model_validate(i) for i in run.items),
        key=lambda i: (i.icp_score or 0.0),
        reverse=True,
    )
    return out


@router.get(
    "/{campaign_id}/bandit",
    response_model=list[ArmPosterior],
    dependencies=[Depends(require_scope("read"))],
)
def bandit_posteriors(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    """Posterior data for the dashboard's converging-Beta-PDF viz."""
    # 404 for an invisible campaign (M5.1d convention: foreign id ≡ nonexistent)
    if db.get(Campaign, campaign_id) is None:
        raise HTTPException(404, "campaign not found")
    rows = db.execute(
        select(Variant, SequenceStep.step_order)
        .join(SequenceStep, Variant.step_id == SequenceStep.id)
        .where(SequenceStep.campaign_id == campaign_id)
    ).all()
    return [
        ArmPosterior(
            variant_id=v.id,
            name=v.name,
            step_order=step_order,
            alpha=v.alpha,
            beta=v.beta,
            active=v.active,
            trials=int(v.alpha + v.beta - 2),
            posterior_mean=v.alpha / (v.alpha + v.beta),
        )
        for v, step_order in rows
    ]


# ---------------------------------------------------------------- escalation rules (M4.2)


@router.get(
    "/{campaign_id}/escalation-rules",
    response_model=list[EscalationRuleOut],
    dependencies=[Depends(require_scope("read"))],
)
def list_escalation_rules(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    """Effective ruleset: built-in defaults (read-only, `builtin: true`) + global
    rules (campaign_id null) + this campaign's rules. DB rules ADD to the defaults;
    the legal-threat tripwire cannot be disabled from data."""
    from sqlalchemy import or_

    from craftsman.inbox.escalation import default_rules

    if db.get(Campaign, campaign_id) is None:
        raise HTTPException(404, "campaign not found")
    out: list[EscalationRuleOut] = []
    for rule in default_rules(get_settings().classifier_confidence_threshold):
        out.append(
            EscalationRuleOut(
                id=uuid.uuid5(uuid.NAMESPACE_URL, f"craftsman:builtin:{rule.name}"),
                campaign_id=None,
                name=rule.name,
                priority=rule.priority,
                enabled=True,
                match={
                    "classifications": list(rule.classifications) if rule.classifications else None,
                    "min_confidence": rule.min_confidence,
                    "max_confidence": rule.max_confidence,
                    "keywords_any": list(rule.keywords_any) if rule.keywords_any else None,
                },
                actions={
                    "notify": rule.notify,
                    "urgent_notify": rule.urgent_notify,
                    "suppress": rule.suppress,
                    "review_queue": rule.review_queue,
                    "block_draft": rule.block_draft,
                    "block_autopilot": rule.block_autopilot,
                },
                builtin=True,
            )
        )
    rows = db.scalars(
        select(EscalationRule)
        .where(
            or_(
                EscalationRule.campaign_id.is_(None),
                EscalationRule.campaign_id == campaign_id,
            )
        )
        .order_by(EscalationRule.priority, EscalationRule.created_at)
    ).all()
    out.extend(EscalationRuleOut.model_validate(r) for r in rows)
    return out


@router.post(
    "/{campaign_id}/escalation-rules",
    response_model=EscalationRuleOut,
    status_code=201,
    dependencies=[Depends(require_scope("operate"))],
)
def create_escalation_rule(
    campaign_id: uuid.UUID, body: EscalationRuleCreate, db: Session = Depends(get_db)
):
    """Add a rule for this campaign. Matching is AND across the given conditions
    (null = wildcard); the decision applied to a reply is the UNION of every
    matching rule's actions — a new rule can never shadow the built-in tripwire."""
    if db.get(Campaign, campaign_id) is None:
        raise HTTPException(404, "campaign not found")
    rule = EscalationRule(
        campaign_id=campaign_id,
        name=body.name,
        priority=body.priority,
        enabled=body.enabled,
        match=body.match.model_dump(),
        actions=body.actions.model_dump(),
    )
    db.add(rule)
    db.flush()
    return EscalationRuleOut.model_validate(rule)


@router.delete(
    "/{campaign_id}/escalation-rules/{rule_id}",
    status_code=204,
    dependencies=[Depends(require_scope("operate"))],
)
def delete_escalation_rule(
    campaign_id: uuid.UUID, rule_id: uuid.UUID, db: Session = Depends(get_db)
):
    rule = db.get(EscalationRule, rule_id)
    if rule is None or rule.campaign_id != campaign_id:
        raise HTTPException(404, "escalation rule not found")
    db.delete(rule)


# ---------------------------------------------------------------- guarded autopilot (M4.4)


@router.post(
    "/{campaign_id}/autopilot/enable",
    dependencies=[Depends(require_scope("admin"))],
)
def enable_autopilot(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    """⛔ Gate M4 Option B. ADMIN-scoped by design — deliberate friction: the
    dashboard's read+operate key cannot flip this; a human with the admin key must.
    What it enables: auto-send of validated, template-constrained drafts for the
    three deterministic intents only, confidence ≥ AUTOPILOT_MIN_CONFIDENCE, no
    escalation match, business hours, ≤ 1 auto-reply per thread ever."""
    from craftsman.core.models import AuditLog

    campaign = _get_campaign_or_404(db, campaign_id)
    campaign.autopilot_enabled = True
    db.add(campaign)
    db.add(AuditLog(event="autopilot_enabled", detail={"campaign_id": str(campaign_id)}))
    db.flush()
    return {"autopilot_enabled": True}


@router.post(
    "/{campaign_id}/autopilot/disable",
    dependencies=[Depends(require_scope("operate"))],
)
def disable_autopilot(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    """The kill switch — OPERATE-scoped (easier to stop than to start), instant:
    the flag is read fresh at every policy evaluation."""
    from craftsman.core.models import AuditLog

    campaign = _get_campaign_or_404(db, campaign_id)
    campaign.autopilot_enabled = False
    db.add(campaign)
    db.add(AuditLog(event="autopilot_disabled", detail={"campaign_id": str(campaign_id)}))
    db.flush()
    return {"autopilot_enabled": False}
