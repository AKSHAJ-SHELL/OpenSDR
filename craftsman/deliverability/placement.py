"""Inbox placement smoke test (M5.3, G12) — BYO seed addresses, honest by design:
there is no proprietary seed network to fake, the operator brings mailboxes they
own and checks them themselves.

Design decisions, recorded:

- **Content**: the campaign's step-1 winning (highest posterior mean) or first
  active variant skeleton, rendered with CONSTANT sample slot values — no LLM
  call, and the validator is not consulted: nothing here ever reaches a prospect,
  so there is no fill to gate. Every mail carries an ``X-Craftsman-Placement:
  <run-id>`` header so seed inboxes (and future IMAP crawlers) can identify it.
- **Suppression**: a suppressed seed address still receives the placement mail.
  Suppression is a prospect-protection concept — do-not-contact lists protect
  people we prospect to; seeds are operator-owned test accounts the operator
  explicitly submitted for this run. Refusing would only make the smoke test
  lie about coverage.
- **What a placement send touches**: the mailbox rate slot (acquire_send_slot),
  ``mailbox.sent_today`` and the per-domain sends rollup (a real send from the
  box spends real reputation and warmup budget), and the audit log. What it
  NEVER touches: campaign daily caps, org send caps, bandit posteriors,
  enrollments, Message rows, or suppression state.
- **Verdicts** are marked manually by the operator (inbox/spam/missing) via
  ``POST /deliverability/placement/{run_id}/mark`` — v1 is manual marking;
  IMAP-crawl automation over seed credentials is future work.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.copywriter.fill import _signature, render_skeleton, split_subject_body
from craftsman.core.models import (
    AuditLog,
    Campaign,
    PlacementResult,
    PlacementRun,
    SequenceStep,
    Variant,
)
from craftsman.sender.limiter import acquire_send_slot
from craftsman.sender.smtp import PreparedEmail, build_email, deliver, pick_mailbox

log = logging.getLogger(__name__)

PLACEMENT_HEADER = "X-Craftsman-Placement"

# Constant sample fills — deliberately self-identifying, shaped like a real fill
# (the skeleton, headers, and footer are the real thing; only the slots are canned).
SAMPLE_FILLS = {
    "subject_hook": "deliverability check",
    "personalization_sentence": (
        "This is a placement test from your own outreach system, sent to a seed "
        "address you configured."
    ),
    "value_prop_bridge": (
        "It mirrors the exact shape of a live campaign email so spam filters "
        "judge the real thing."
    ),
    "cta_question": "did this land in the inbox?",
}

# per-seed retries when the mailbox rate slot is busy, before recording an error
_RATE_SLOT_ATTEMPTS = 3


def pick_placement_variant(db: Session, campaign_id) -> Variant | None:
    """Step-1 winning-or-first active variant: highest posterior mean wins;
    untried arms all tie at 0.5 and the first (creation order) is kept."""
    step = db.scalar(
        select(SequenceStep).where(
            SequenceStep.campaign_id == campaign_id, SequenceStep.step_order == 1
        )
    )
    if step is None:
        return None
    variants = list(
        db.scalars(select(Variant).where(Variant.step_id == step.id, Variant.active))
    )
    if not variants:
        return None
    best = variants[0]
    for v in variants[1:]:
        if v.alpha / (v.alpha + v.beta) > best.alpha / (best.alpha + best.beta):
            best = v
    return best


@dataclass
class RenderedPlacement:
    subject: str
    body: str


def render_placement(variant: Variant, campaign: Campaign) -> RenderedPlacement:
    static = {"first_name": "there", "signature": _signature(campaign.sender_persona or {})}
    rendered = render_skeleton(variant.skeleton, SAMPLE_FILLS, static)
    subject, body = split_subject_body(rendered)
    return RenderedPlacement(subject=subject or SAMPLE_FILLS["subject_hook"], body=body)


async def execute_placement_run(db: Session, run: PlacementRun) -> None:
    """Deliver the rendered opener to every pending seed through the real send
    engine. Runs inside the run's org context (the worker task enters it)."""
    campaign = db.get(Campaign, run.campaign_id)
    variant = pick_placement_variant(db, run.campaign_id) if campaign else None
    if campaign is None or variant is None:
        run.status = "failed"
        run.error = "campaign or step-1 active variant missing"
        run.finished_at = datetime.now(timezone.utc)
        db.add(run)
        return

    rendered = render_placement(variant, campaign)
    results = list(
        db.scalars(
            select(PlacementResult).where(
                PlacementResult.run_id == run.id, PlacementResult.verdict == "pending"
            )
        )
    )
    for result in results:
        try:
            mailbox = pick_mailbox(db)
            if mailbox is None:
                result.error = "no_mailbox_capacity"
                db.add(result)
                db.commit()
                continue
            wait = _acquire_slot_with_patience(str(mailbox.id))
            if wait > 0:
                result.error = "rate_limited"
                db.add(result)
                db.commit()
                continue

            prepared = PreparedEmail(
                subject=rendered.subject, body=rendered.body, to_email=result.seed_email
            )
            # build_email only reads .email from its lead argument (unsubscribe
            # token + To). The stub keeps full header parity with campaign mail —
            # placement must be judged exactly as the real thing would be.
            msg, _message_id = build_email(
                db=db,
                mailbox=mailbox,
                lead=SimpleNamespace(email=result.seed_email),  # type: ignore[arg-type]
                prepared=prepared,
            )
            msg[PLACEMENT_HEADER] = str(run.id)
            await deliver(mailbox, msg)

            result.mailbox_id = mailbox.id
            result.delivered = True
            result.error = None
            db.add(result)
            mailbox.sent_today += 1  # a real send spends real warmup budget
            db.add(mailbox)
            from craftsman.deliverability.health import record_domain_send
            from craftsman.ingest.verify import domain_of

            record_domain_send(db, domain_of(mailbox.email))
        except Exception as e:  # noqa: BLE001 — one seed never sinks the run
            result.error = str(e)
            db.add(result)
        db.commit()  # each seed durable as it lands

    run.status = "complete"
    run.finished_at = datetime.now(timezone.utc)
    db.add(run)
    db.add(
        AuditLog(
            event="placement_run_complete",
            detail={
                "run_id": str(run.id),
                "campaign_id": str(run.campaign_id),
                "variant_id": str(variant.id),
                "seeds": len(results),
                "delivered": sum(1 for r in results if r.delivered),
            },
        )
    )


def _acquire_slot_with_patience(mailbox_id: str) -> float:
    """Placement respects the mailbox rate bucket like any other send, but runs in
    a worker with nobody waiting — so it sleeps through a busy slot a few times
    (bounded: ≤10 seeds per run) before giving up with the wait it last saw."""
    wait = acquire_send_slot(mailbox_id)
    for _ in range(_RATE_SLOT_ATTEMPTS):
        if wait <= 0:
            return 0.0
        time.sleep(min(wait, 90.0) + 0.1)
        wait = acquire_send_slot(mailbox_id)
    return wait
