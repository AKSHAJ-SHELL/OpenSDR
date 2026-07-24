"""Deliverability suite endpoints (M5.3, G12): per-domain health + placement runs.

/deliverability/domains mirrors the M1.4 per-mailbox report's posture — live DNS
at request time, nothing persisted — but aggregates per sending DOMAIN and folds
in the rollup stats and DNSBL verdicts behind the documented health score. Every
DNS call goes through the dns_auth / health seams, so tests never touch the
network. Placement decisions (sample fills, suppression stance, manual marking)
are recorded in deliverability/placement.py.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.core.models import (
    AuditLog,
    Campaign,
    Mailbox,
    PlacementResult,
    PlacementRun,
)
from craftsman.core.schemas import (
    BlocklistOut,
    DkimOut,
    DmarcOut,
    DnsRecordOut,
    DomainHealthOut,
    DomainStats7dOut,
    PlacementCreate,
    PlacementMarkRequest,
    PlacementRunOut,
)
from craftsman.deliverability import health as health_mod
from craftsman.deliverability.dns_auth import check_dkim, check_dmarc, check_spf
from craftsman.deliverability.placement import pick_placement_variant
from craftsman.ingest.verify import domain_of

router = APIRouter(prefix="/deliverability", tags=["deliverability"])


@router.get(
    "/domains",
    response_model=list[DomainHealthOut],
    dependencies=[Depends(require_scope("read"))],
)
def domain_health(db: Session = Depends(get_db)):
    """One entry per distinct sending domain of the org's mailboxes: DNS auth,
    blocklist verdicts, 7-day send/bounce counts, and the 0-100 score."""
    boxes = list(db.scalars(select(Mailbox)))
    by_domain: dict[str, list[Mailbox]] = {}
    for box in boxes:
        by_domain.setdefault(domain_of(box.email), []).append(box)

    zones = health_mod.blocklist_zones()
    out: list[DomainHealthOut] = []
    for domain in sorted(by_domain):
        domain_boxes = by_domain[domain]
        # a stored selector is authoritative for the whole domain's DKIM check
        selector = next((b.dkim_selector for b in domain_boxes if b.dkim_selector), None)
        spf = check_spf(domain)
        dmarc = check_dmarc(domain)
        dkim = check_dkim(domain, selector)
        verdicts = health_mod.check_blocklists(domain, zones)
        stats = health_mod.seven_day_stats(db, domain)
        score, components = health_mod.health_score(
            spf_status=spf.status,
            dkim_status=dkim.status,
            dmarc_status=dmarc.status,
            blocklist_listings=sum(1 for v in verdicts if v.status == health_mod.LISTED),
            stats=stats,
        )
        out.append(
            DomainHealthOut(
                domain=domain,
                score=score,
                components=components,
                mailboxes=len(domain_boxes),
                paused_mailboxes=sum(1 for b in domain_boxes if b.health == "paused"),
                spf=DnsRecordOut(
                    status=spf.status, record=spf.record, recommended=spf.recommended
                ),
                dmarc=DmarcOut(
                    status=dmarc.status, policy=dmarc.policy, record=dmarc.record,
                    recommended=dmarc.recommended,
                ),
                dkim=DkimOut(status=dkim.status, selector=dkim.selector, record=dkim.record),
                blocklists=[
                    BlocklistOut(zone=v.zone, status=v.status, listed_ips=v.listed_ips)
                    for v in verdicts
                ],
                stats_7d=DomainStats7dOut(
                    sends=stats.sends,
                    hard_bounces=stats.hard_bounces,
                    spam_bounces=stats.spam_bounces,
                    bounce_rate=stats.bounce_rate,
                    complaint_rate=stats.complaint_rate,
                ),
            )
        )
    return out


# ------------------------------------------------------------------ placement


def _get_run_or_404(db: Session, run_id: uuid.UUID) -> PlacementRun:
    run = db.scalar(
        select(PlacementRun)
        .where(PlacementRun.id == run_id)
        .options(selectinload(PlacementRun.results))
    )
    if run is None:
        raise HTTPException(404, "placement run not found")
    return run


@router.post(
    "/placement",
    response_model=PlacementRunOut,
    status_code=202,
    dependencies=[Depends(require_scope("operate"))],
)
def start_placement(payload: PlacementCreate, db: Session = Depends(get_db)):
    """Create a placement run (per-seed verdict `pending`) and hand it to the
    worker. Seed count/shape limits are schema-enforced (≤10, valid emails)."""
    campaign = db.get(Campaign, payload.campaign_id)
    if campaign is None:
        raise HTTPException(404, "campaign not found")
    if pick_placement_variant(db, campaign.id) is None:
        raise HTTPException(400, "campaign has no active variants on step 1; add one first")
    if not db.scalar(select(Mailbox.id).where(Mailbox.health != "paused").limit(1)):
        raise HTTPException(400, "no unpaused mailbox to send from")

    run = PlacementRun(campaign_id=campaign.id, status="running")
    db.add(run)
    db.flush()
    # dedupe while preserving order — a repeated seed is one send, one verdict
    for seed in dict.fromkeys(e.lower() for e in payload.seed_emails):
        db.add(PlacementResult(run_id=run.id, seed_email=seed))
    db.add(
        AuditLog(
            event="placement_run_started",
            detail={
                "run_id": str(run.id),
                "campaign_id": str(campaign.id),
                "seeds": len(set(e.lower() for e in payload.seed_emails)),
            },
        )
    )
    db.commit()  # durable before the worker looks for it

    from craftsman.workers.tasks import run_placement

    run_placement.delay(str(run.id))
    return _get_run_or_404(db, run.id)


@router.get(
    "/placement",
    response_model=list[PlacementRunOut],
    dependencies=[Depends(require_scope("read"))],
)
def list_placement_runs(limit: int = 20, db: Session = Depends(get_db)):
    runs = db.scalars(
        select(PlacementRun)
        .order_by(PlacementRun.created_at.desc())
        .limit(max(1, min(limit, 100)))
        .options(selectinload(PlacementRun.results))
    )
    return list(runs)


@router.get(
    "/placement/{run_id}",
    response_model=PlacementRunOut,
    dependencies=[Depends(require_scope("read"))],
)
def get_placement_run(run_id: uuid.UUID, db: Session = Depends(get_db)):
    return _get_run_or_404(db, run_id)


@router.post(
    "/placement/{run_id}/mark",
    response_model=PlacementRunOut,
    dependencies=[Depends(require_scope("operate"))],
)
def mark_placement_run(
    run_id: uuid.UUID, payload: PlacementMarkRequest, db: Session = Depends(get_db)
):
    """Operator verdicts per seed (inbox/spam/missing) — they checked the seed
    mailboxes themselves. Re-marking is allowed (corrections are honest data)."""
    run = _get_run_or_404(db, run_id)
    by_seed = {r.seed_email: r for r in run.results}
    unknown = [s for s in payload.marks if s.lower() not in by_seed]
    if unknown:
        raise HTTPException(400, f"unknown seed address(es) for this run: {unknown}")
    now = datetime.now(timezone.utc)
    for seed, verdict in payload.marks.items():
        result = by_seed[seed.lower()]
        result.verdict = verdict
        result.marked_at = now
        db.add(result)
    db.flush()
    return run
