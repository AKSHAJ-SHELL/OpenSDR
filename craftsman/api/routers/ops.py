"""Operational visibility endpoints: dead letters and the audit-log export (M5.4)."""

import json
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.core.models import AuditLog, DeadLetter
from craftsman.core.schemas import DeadLetterOut
from craftsman.core.tenancy import org_context, require_org_id

router = APIRouter(tags=["ops"])


@router.get(
    "/dead-letters",
    response_model=list[DeadLetterOut],
    dependencies=[Depends(require_scope("read"))],
)
def list_dead_letters(limit: int = 50, db: Session = Depends(get_db)):
    # DeadLetter is deliberately not OrgScoped (see the model), so this view
    # filters manually: the caller's org plus unattributed (NULL) infra failures
    return list(
        db.scalars(
            select(DeadLetter)
            .where(
                (DeadLetter.org_id == require_org_id()) | (DeadLetter.org_id.is_(None))
            )
            .order_by(DeadLetter.created_at.desc())
            .limit(limit)
        ).all()
    )


@router.get("/audit/export", dependencies=[Depends(require_scope("admin"))])
def export_audit_log(since: datetime | None = None, db: Session = Depends(get_db)):
    """NDJSON stream of the caller org's audit_log, oldest first — one JSON
    object per line, the shape SIEMs and jq ingest without ceremony. Optional
    `?since=` (ISO timestamp) lower-bounds `created_at` for incremental pulls.

    AuditLog is OrgScoped, so the query can only ever see the caller's org.
    The streaming body runs after the request handler (and its contextvars)
    returned — each __next__ may execute in a fresh context — so the org
    context is re-entered around each PAGE fetch (keyset pagination), never
    held open across a yield."""
    oid = require_org_id()
    page_size = 500

    def _fetch_page(cursor):
        # entered and exited entirely within one generator step — a contextvar
        # token must be reset in the context that created it
        with org_context(oid):
            stmt = (
                select(AuditLog)
                .order_by(AuditLog.created_at, AuditLog.id)
                .limit(page_size)
            )
            if since is not None:
                stmt = stmt.where(AuditLog.created_at >= since)
            if cursor is not None:
                stmt = stmt.where(tuple_(AuditLog.created_at, AuditLog.id) > cursor)
            return db.scalars(stmt).all()

    def _lines():
        cursor = None
        while True:
            rows = _fetch_page(cursor)
            if not rows:
                return
            for row in rows:
                yield json.dumps(
                    {
                        "id": str(row.id),
                        "enrollment_id": (
                            str(row.enrollment_id) if row.enrollment_id else None
                        ),
                        "event": row.event,
                        "from_state": row.from_state,
                        "to_state": row.to_state,
                        "detail": row.detail,
                        "created_at": (
                            row.created_at.isoformat() if row.created_at else None
                        ),
                    },
                    default=str,
                ) + "\n"
            cursor = (rows[-1].created_at, rows[-1].id)

    return StreamingResponse(_lines(), media_type="application/x-ndjson")
