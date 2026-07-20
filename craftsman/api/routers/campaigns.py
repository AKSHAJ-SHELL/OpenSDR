import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.deps import get_db
from craftsman.core.config import get_settings
from craftsman.core.models import Campaign, Enrollment, Lead, SequenceStep, Variant
from craftsman.core.schemas import (
    ArmPosterior,
    CampaignCreate,
    CampaignOut,
    VariantCreate,
    VariantOut,
)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.get("", response_model=list[CampaignOut])
def list_campaigns(db: Session = Depends(get_db)):
    return list(db.scalars(select(Campaign).order_by(Campaign.name)).all())


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")
    return campaign


@router.post("", response_model=CampaignOut)
async def create_campaign(payload: CampaignCreate, db: Session = Depends(get_db)):
    from craftsman.scoring.embeddings import get_embedder

    campaign = Campaign(
        name=payload.name,
        icp_description=payload.icp_description,
        value_prop=payload.value_prop,
        sender_persona=payload.sender_persona,
        daily_cap=payload.daily_cap,
    )
    embedder = get_embedder()
    campaign.icp_embedding = (await embedder.embed([payload.icp_description]))[0]
    db.add(campaign)
    db.flush()
    for i, wait_days in enumerate(payload.steps, start=1):
        db.add(SequenceStep(campaign_id=campaign.id, step_order=i, wait_days=wait_days))
    return campaign


@router.post("/{campaign_id}/variants", response_model=VariantOut)
def add_variant(campaign_id: uuid.UUID, payload: VariantCreate, db: Session = Depends(get_db)):
    step = db.scalar(
        select(SequenceStep).where(
            SequenceStep.campaign_id == campaign_id,
            SequenceStep.step_order == payload.step_order,
        )
    )
    if step is None:
        raise HTTPException(404, f"no step {payload.step_order} in campaign")
    variant = Variant(
        step_id=step.id, name=payload.name,
        skeleton=payload.skeleton, slot_schema=payload.slot_schema,
    )
    db.add(variant)
    db.flush()
    return variant


@router.post("/{campaign_id}/activate", response_model=CampaignOut)
async def activate(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    """Activate: score all verified leads against the ICP and enroll those above threshold."""
    from craftsman.scoring.embeddings import get_embedder
    from craftsman.scoring.icp import icp_score, lead_text

    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")
    steps = db.scalars(select(SequenceStep).where(SequenceStep.campaign_id == campaign_id)).all()
    has_variants = any(
        db.scalar(select(Variant.id).where(Variant.step_id == s.id).limit(1)) for s in steps
    )
    if not has_variants:
        raise HTTPException(400, "campaign has no variants; add at least one per step")

    settings = get_settings()
    embedder = get_embedder()
    icp_emb = list(campaign.icp_embedding) if campaign.icp_embedding is not None else (
        await embedder.embed([campaign.icp_description])
    )[0]

    leads = db.scalars(
        select(Lead).where(Lead.email_verified.is_(True), Lead.status == "verified")
    ).all()
    enrolled = 0
    for lead in leads:
        company = lead.company
        text = lead_text(lead.title, company.name if company else None, None)
        lead_emb = (await embedder.embed([text]))[0]
        score = icp_score(lead_emb, icp_emb, lead.title)
        lead.icp_score = score
        if score < settings.icp_threshold:
            lead.status = "disqualified"
            db.add(lead)
            continue
        db.add(lead)
        exists = db.scalar(
            select(Enrollment.id).where(
                Enrollment.lead_id == lead.id, Enrollment.campaign_id == campaign.id
            )
        )
        if exists is None:
            db.add(
                Enrollment(
                    lead_id=lead.id,
                    campaign_id=campaign.id,
                    state="queued",
                    current_step=0,
                    next_action_at=datetime.now(timezone.utc),
                )
            )
            enrolled += 1

    campaign.status = "active"
    db.add(campaign)
    return campaign


@router.post("/{campaign_id}/pause", response_model=CampaignOut)
def pause(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")
    campaign.status = "paused"
    db.add(campaign)
    return campaign


@router.get("/{campaign_id}/bandit", response_model=list[ArmPosterior])
def bandit_posteriors(campaign_id: uuid.UUID, db: Session = Depends(get_db)):
    """Posterior data for the dashboard's converging-Beta-PDF viz."""
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
