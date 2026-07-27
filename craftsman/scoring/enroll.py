"""Score-and-enroll: the ONE path a lead takes into a campaign.

Extracted verbatim from the activate endpoint's loop (M5.2 refactor) so CRM
list imports can enroll through the same ICP gate instead of growing a second,
subtly different enrollment path. Behavior is identical: score every given
lead against the campaign ICP (2-way or 3-way blend per M2.3), disqualify
below threshold, enroll the rest exactly once per (lead, campaign).
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.core.config import get_settings
from craftsman.core.models import Campaign, Enrollment, Lead


async def score_and_enroll(db: Session, campaign: Campaign, leads: list[Lead]) -> int:
    """Score `leads` against `campaign`'s ICP; enroll those above threshold.
    Returns the number of new enrollments. Callers pass verified leads —
    unverified ones would be scored on garbage (no title/company confidence)."""
    from craftsman.scoring.embeddings import get_embedder
    from craftsman.scoring.icp import lead_text, score_breakdown
    from craftsman.scoring.rules import company_signal_boost

    settings = get_settings()
    embedder = get_embedder()
    icp_emb = list(campaign.icp_embedding) if campaign.icp_embedding is not None else (
        await embedder.embed([campaign.icp_description])
    )[0]

    enrolled = 0
    scored_at = datetime.now(timezone.utc)
    for lead in leads:
        company = lead.company
        text = lead_text(lead.title, company.name if company else None, None)
        lead_emb = (await embedder.embed([text]))[0]
        # None ⇒ company has no signals ⇒ 2-way blend (unchanged); value ⇒ 3-way (M2.3)
        boost = company_signal_boost(db, lead.company_id, scored_at, settings.signal_half_life_days)
        breakdown = score_breakdown(lead_emb, icp_emb, lead.title, boost)
        score = breakdown.score
        lead.icp_score = score
        # provenance: which ICP produced this, and from what parts
        lead.icp_cosine = breakdown.cosine
        lead.icp_rule = breakdown.rule
        lead.icp_signal = breakdown.signal  # None when no signals
        lead.icp_scored_campaign_id = campaign.id
        lead.icp_scored_at = scored_at
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
    return enrolled
