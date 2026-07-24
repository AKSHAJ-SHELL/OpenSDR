"""The caller's org (M5.1c): identity, quotas, and today's usage — read-only.

Quotas are imposed by the instance operator via `python -m craftsman.manage_org`
on the box; a tenant that could raise its own caps would not have caps.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.core.models import Mailbox, Org
from craftsman.core.schemas import OrgOut
from craftsman.core.tenancy import require_org_id

router = APIRouter(tags=["org"])


@router.get("/org", response_model=OrgOut, dependencies=[Depends(require_scope("read"))])
def get_org(db: Session = Depends(get_db)):
    org = db.get(Org, require_org_id())
    if org is None:
        raise HTTPException(500, "authenticated org row is missing")
    return OrgOut(
        id=org.id,
        name=org.name,
        slug=org.slug,
        daily_send_cap=org.daily_send_cap,
        sent_today=org.sent_today,
        max_mailboxes=org.max_mailboxes,
        mailbox_count=db.scalar(select(func.count(Mailbox.id))) or 0,
        enrichment_daily_budget=org.enrichment_daily_budget,
        enrichment_calls_today=org.enrichment_calls_today,
    )
