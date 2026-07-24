"""Outbound webhook events (M5.4): the fixed registry and the emission helper.

The registry is deliberately closed — an event type not listed here is refused
both at emission (`emit_event` raises) and at endpoint registration (the API
422s an unknown mask entry). Growing it is a design decision, not a data entry.

Event types:

- ``lead.status_changed`` — an enrollment/lead moved state. **Granularity,
  honestly:** this fires from exactly two places — the reply pipeline's
  confident-classification state transitions (`apply_classification`) and the
  manual ``POST /leads/{id}/suppress`` endpoint. Lead status flips inside the
  ingest gate, bulk import, verification, or GDPR erasure do NOT emit; wiring
  emission through every status write would thread webhook concerns into paths
  that must stay simple (and erasure deliberately leaves no event trail).
- ``reply.received`` — an inbound reply was matched to a thread and classified
  (payload carries the label + confidence, including low-confidence ones that
  went to the review queue).
- ``meeting.updated`` — a calendar-provider webhook created/updated a meeting.
- ``autopilot.sent`` — Guarded Autopilot dispatched a validated reply draft.
- ``escalation.fired`` — the escalation ruleset matched with at least one action.

Emission never breaks the emitting path: call sites go through
:func:`safe_emit`, which swallows (and logs) every failure — no endpoints, a
down broker, or a tenancy misstep is a no-op for the pipeline, never an error.

Delivery is asynchronous: `emit_event` creates one `webhook_deliveries` row per
subscribed active endpoint in the caller's org (the endpoint lookup is
tenancy-scoped automatically) and enqueues the Celery delivery task per row.
The enqueue rides on the caller's open transaction; a task racing the commit
finds no row and no-ops — the pending row stays visible in the deliveries
listing either way, so nothing fails silently.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.core.models import WebhookDelivery, WebhookEndpoint

log = logging.getLogger(__name__)

EVENT_TYPES: tuple[str, ...] = (
    "lead.status_changed",
    "reply.received",
    "meeting.updated",
    "autopilot.sent",
    "escalation.fired",
)

# Synthetic endpoint-test event (POST /webhooks/{id}/test). Deliberately NOT in
# EVENT_TYPES: it can never be subscribed to or emitted by emit_event — the test
# endpoint targets one endpoint directly, mask or no mask.
PING_EVENT = "ping"


def emit_event(db: Session, event_type: str, payload: dict) -> int:
    """Create delivery rows for every active endpoint in the current org whose
    mask includes ``event_type``, and enqueue the delivery task for each.
    Returns the number of deliveries created. Raises ValueError for an event
    type outside the registry — a forged/typo'd type must fail loudly in tests,
    which is why call sites wrap this in :func:`safe_emit`."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown webhook event type: {event_type!r}")
    endpoints = db.scalars(
        select(WebhookEndpoint).where(WebhookEndpoint.active)
    ).all()  # org-scoped by the tenancy layer
    created = 0
    for endpoint in endpoints:
        if event_type not in (endpoint.event_mask or []):
            continue
        delivery = WebhookDelivery(
            endpoint_id=endpoint.id, event_type=event_type, payload=payload
        )
        db.add(delivery)
        db.flush()
        _enqueue_delivery(str(delivery.id))
        created += 1
    return created


def safe_emit(db: Session, event_type: str, payload: dict) -> int:
    """`emit_event`, guaranteed non-throwing — the only form call sites in the
    pipeline/send/meeting paths may use. A webhook problem is never a reason
    for a classification, send, or booking to fail."""
    try:
        return emit_event(db, event_type, payload)
    except Exception as e:  # noqa: BLE001 — emission must never break the emitter
        log.warning("webhook emission failed for %s: %s", event_type, e)
        return 0


def _enqueue_delivery(delivery_id: str) -> None:
    """Enqueue the Celery delivery task (lazy import: pure modules that emit
    events never import Celery at module load). A broker failure is logged and
    swallowed — the pending delivery row remains as the honest record."""
    try:
        from craftsman.workers.tasks import deliver_webhook

        deliver_webhook.delay(delivery_id)
    except Exception as e:  # noqa: BLE001
        log.warning("webhook delivery enqueue failed for %s: %s", delivery_id, e)
