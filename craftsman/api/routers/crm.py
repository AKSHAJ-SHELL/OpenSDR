"""CRM sync API (M5.2, G10). Credentials are write-only (admin scope):
validated and Fernet-encrypted at write, never present in any response.
Import is dry-run by default — committing customer data is the explicit path.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.core.models import Campaign, CRMConnection, CRMSyncRun
from craftsman.core.schemas import (
    CRMConnectionCreate,
    CRMConnectionOut,
    CRMConnectionUpdate,
    CRMImportOut,
    CRMImportRequest,
    CRMListOut,
    CRMPreviewRowOut,
    CRMSyncRunOut,
)
from craftsman.crm.mapping import MappingError, effective_map
from craftsman.crm.sync import (
    build_crm_client,
    commit_import,
    encrypt_credentials,
    preview_import,
    push_activity,
    record_run,
)
from craftsman.research.fetch import UnsafeURL, validate_url

router = APIRouter(prefix="/crm", tags=["crm"])

_REQUIRED_CREDS = {
    "hubspot": ("access_token",),
    "salesforce": ("instance_url", "client_id", "client_secret"),
}


def _check_credentials(provider: str, credentials: dict) -> None:
    missing = [k for k in _REQUIRED_CREDS[provider] if not credentials.get(k)]
    if missing:
        raise HTTPException(422, f"missing credential field(s) for {provider}: {missing}")
    if provider == "salesforce":
        try:
            # SSRF: operator-supplied URL is checked at write AND at every use
            validate_url(credentials["instance_url"])
        except UnsafeURL as e:
            raise HTTPException(422, f"instance_url refused: {e}")


def _check_field_map(provider: str, overlay: dict) -> None:
    try:
        effective_map(provider, overlay)
    except MappingError as e:
        raise HTTPException(422, str(e))


def _get_connection_or_404(db: Session, connection_id: uuid.UUID) -> CRMConnection:
    connection = db.get(CRMConnection, connection_id)
    if connection is None:
        raise HTTPException(404, "connection not found")
    return connection


@router.post(
    "/connections",
    response_model=CRMConnectionOut,
    status_code=201,
    dependencies=[Depends(require_scope("admin"))],
)
def create_connection(payload: CRMConnectionCreate, db: Session = Depends(get_db)):
    _check_credentials(payload.provider, payload.credentials)
    _check_field_map(payload.provider, payload.field_map)
    connection = CRMConnection(
        provider=payload.provider,
        name=payload.name,
        credentials_enc=encrypt_credentials(payload.credentials),
        field_map=payload.field_map,
    )
    db.add(connection)
    db.flush()
    return connection


@router.get(
    "/connections",
    response_model=list[CRMConnectionOut],
    dependencies=[Depends(require_scope("read"))],
)
def list_connections(db: Session = Depends(get_db)):
    return list(db.scalars(select(CRMConnection).order_by(CRMConnection.created_at)).all())


@router.patch(
    "/connections/{connection_id}",
    response_model=CRMConnectionOut,
    dependencies=[Depends(require_scope("admin"))],
)
def update_connection(
    connection_id: uuid.UUID, payload: CRMConnectionUpdate, db: Session = Depends(get_db)
):
    connection = _get_connection_or_404(db, connection_id)
    changes = payload.model_dump(exclude_unset=True)
    if "credentials" in changes:
        _check_credentials(connection.provider, changes["credentials"])
        connection.credentials_enc = encrypt_credentials(changes.pop("credentials"))
    if "field_map" in changes:
        _check_field_map(connection.provider, changes["field_map"])
    for field, value in changes.items():
        setattr(connection, field, value)
    db.add(connection)
    return connection


@router.post(
    "/connections/{connection_id}/test",
    dependencies=[Depends(require_scope("admin"))],
)
async def test_connection(connection_id: uuid.UUID, db: Session = Depends(get_db)):
    """Cheap authenticated call against the CRM; surfaces the failure text
    instead of a bare 500 — this is the 'is my token right?' button."""
    connection = _get_connection_or_404(db, connection_id)
    try:
        detail = await build_crm_client(connection).test()
        return {"ok": True, "detail": detail}
    except Exception as e:  # noqa: BLE001 — the point is reporting the failure
        return {"ok": False, "detail": str(e)}


@router.get(
    "/connections/{connection_id}/lists",
    response_model=list[CRMListOut],
    dependencies=[Depends(require_scope("operate"))],
)
async def importable_lists(connection_id: uuid.UUID, db: Session = Depends(get_db)):
    connection = _get_connection_or_404(db, connection_id)
    refs = await build_crm_client(connection).lists()
    return [CRMListOut(remote_id=r.remote_id, name=r.name, size=r.size) for r in refs]


@router.post(
    "/connections/{connection_id}/import",
    response_model=CRMImportOut,
    dependencies=[Depends(require_scope("operate"))],
)
async def import_list(
    connection_id: uuid.UUID, payload: CRMImportRequest, db: Session = Depends(get_db)
):
    connection = _get_connection_or_404(db, connection_id)
    campaign = None
    if payload.campaign_id is not None:
        campaign = db.get(Campaign, payload.campaign_id)
        if campaign is None:
            raise HTTPException(404, "campaign not found")

    contacts = await build_crm_client(connection).contacts(payload.list_id)

    if payload.dry_run:
        outcome = preview_import(db, connection, contacts)
        return CRMImportOut(
            dry_run=True,
            stats=outcome.stats,
            preview=[
                CRMPreviewRowOut(email=p.email, action=p.action, changes=p.changes)
                for p in outcome.preview
            ],
        )

    outcome = await commit_import(db, connection, contacts, campaign)
    run = record_run(db, connection, "inbound", outcome.stats)
    return CRMImportOut(dry_run=False, stats=outcome.stats, run_id=run.id)


@router.post(
    "/connections/{connection_id}/sync",
    response_model=CRMSyncRunOut,
    dependencies=[Depends(require_scope("operate"))],
)
async def sync_now(connection_id: uuid.UUID, db: Session = Depends(get_db)):
    """Push activity past the watermark right now (the beat task does the same
    on a schedule)."""
    connection = _get_connection_or_404(db, connection_id)
    try:
        stats = await push_activity(db, connection)
        return record_run(db, connection, "outbound", stats)
    except Exception as e:  # noqa: BLE001 — auth/network failure is a run outcome
        return record_run(db, connection, "outbound", {}, error=str(e))


@router.get(
    "/connections/{connection_id}/runs",
    response_model=list[CRMSyncRunOut],
    dependencies=[Depends(require_scope("read"))],
)
def list_runs(connection_id: uuid.UUID, limit: int = 50, db: Session = Depends(get_db)):
    _get_connection_or_404(db, connection_id)
    return list(
        db.scalars(
            select(CRMSyncRun)
            .where(CRMSyncRun.connection_id == connection_id)
            .order_by(CRMSyncRun.created_at.desc())
            .limit(limit)
        ).all()
    )
