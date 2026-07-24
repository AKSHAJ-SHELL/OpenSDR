"""Outbound webhook management (M5.4).

Scopes follow the plan exactly: reads are `operate` (webhook config is
operational surface, not analytics), writes are `admin`, and the per-endpoint
deliveries log is `read`. The secret is generated server-side, shown exactly
once in the creation response, stored Fernet-encrypted (like mailbox
passwords), and never echoed again — every later view gets only a prefix.

Registration re-uses the M0.5 SSRF guard verbatim: a non-https URL, a
disallowed port, or any resolution to a private/loopback/link-local/metadata
address is a 422. The delivery task re-validates at send time too (DNS can
change after registration) — see workers/tasks.py deliver_webhook.
"""

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.core.crypto import decrypt, encrypt
from craftsman.core.models import WebhookDelivery, WebhookEndpoint
from craftsman.core.schemas import (
    WebhookDeliveryOut,
    WebhookEndpointCreate,
    WebhookEndpointCreated,
    WebhookEndpointOut,
    WebhookEndpointUpdate,
)
from craftsman.research.fetch import UnsafeURL, validate_url
from craftsman.webhooks.events import PING_EVENT

SECRET_PREFIX_CHARS = 10

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _generate_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def _secret_prefix(endpoint: WebhookEndpoint) -> str:
    return decrypt(endpoint.secret_enc)[:SECRET_PREFIX_CHARS] + "…"


def _guard_url(url: str) -> None:
    """422 unless the URL clears the SSRF guard (https-only, public IPs only)."""
    try:
        validate_url(url)
    except UnsafeURL as e:
        raise HTTPException(422, f"webhook URL rejected: {e}")


def _endpoint_out(endpoint: WebhookEndpoint) -> WebhookEndpointOut:
    return WebhookEndpointOut(
        id=endpoint.id,
        url=endpoint.url,
        event_mask=list(endpoint.event_mask or []),
        active=endpoint.active,
        secret_prefix=_secret_prefix(endpoint),
        created_at=endpoint.created_at,
    )


@router.post(
    "",
    response_model=WebhookEndpointCreated,
    status_code=201,
    dependencies=[Depends(require_scope("admin"))],
)
def create_webhook(payload: WebhookEndpointCreate, db: Session = Depends(get_db)):
    _guard_url(payload.url)
    secret = _generate_secret()
    endpoint = WebhookEndpoint(
        url=payload.url,
        secret_enc=encrypt(secret),
        event_mask=payload.event_mask,
    )
    db.add(endpoint)
    db.flush()
    return WebhookEndpointCreated(
        id=endpoint.id,
        url=endpoint.url,
        event_mask=list(endpoint.event_mask),
        active=endpoint.active,
        secret_prefix=secret[:SECRET_PREFIX_CHARS] + "…",
        created_at=endpoint.created_at,
        secret=secret,  # shown exactly once
    )


@router.get(
    "",
    response_model=list[WebhookEndpointOut],
    dependencies=[Depends(require_scope("operate"))],
)
def list_webhooks(db: Session = Depends(get_db)):
    endpoints = db.scalars(
        select(WebhookEndpoint).order_by(WebhookEndpoint.created_at.desc())
    ).all()
    return [_endpoint_out(e) for e in endpoints]


@router.patch(
    "/{endpoint_id}",
    response_model=WebhookEndpointOut,
    dependencies=[Depends(require_scope("admin"))],
)
def update_webhook(
    endpoint_id: uuid.UUID, payload: WebhookEndpointUpdate, db: Session = Depends(get_db)
):
    endpoint = db.get(WebhookEndpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(404, "webhook not found")
    if payload.url is not None:
        _guard_url(payload.url)  # a changed URL earns a fresh SSRF check
        endpoint.url = payload.url
    if payload.event_mask is not None:
        endpoint.event_mask = payload.event_mask
    if payload.active is not None:
        endpoint.active = payload.active
    db.add(endpoint)
    db.flush()
    return _endpoint_out(endpoint)


@router.delete(
    "/{endpoint_id}", status_code=204, dependencies=[Depends(require_scope("admin"))]
)
def delete_webhook(endpoint_id: uuid.UUID, db: Session = Depends(get_db)):
    endpoint = db.get(WebhookEndpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(404, "webhook not found")
    # delivery history goes with the endpoint (no FK cascade per M0.4 doctrine)
    for delivery in db.scalars(
        select(WebhookDelivery).where(WebhookDelivery.endpoint_id == endpoint.id)
    ).all():
        db.delete(delivery)
    db.delete(endpoint)
    db.flush()


@router.post(
    "/{endpoint_id}/test",
    status_code=202,
    dependencies=[Depends(require_scope("admin"))],
)
def test_webhook(endpoint_id: uuid.UUID, db: Session = Depends(get_db)):
    """Queue a synthetic `ping` delivery to this one endpoint — mask and active
    flag deliberately don't apply (you are testing THIS endpoint's plumbing:
    URL reachability, signature verification on the receiver)."""
    endpoint = db.get(WebhookEndpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(404, "webhook not found")
    delivery = WebhookDelivery(
        endpoint_id=endpoint.id,
        event_type=PING_EVENT,
        payload={"ping": True, "endpoint_id": str(endpoint.id)},
    )
    db.add(delivery)
    db.flush()
    from craftsman.webhooks.events import _enqueue_delivery

    _enqueue_delivery(str(delivery.id))
    return {"delivery_id": str(delivery.id), "status": delivery.status}


@router.get(
    "/{endpoint_id}/deliveries",
    response_model=list[WebhookDeliveryOut],
    dependencies=[Depends(require_scope("read"))],
)
def list_deliveries(endpoint_id: uuid.UUID, db: Session = Depends(get_db)):
    endpoint = db.get(WebhookEndpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(404, "webhook not found")
    return list(
        db.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.endpoint_id == endpoint.id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(50)
        ).all()
    )
