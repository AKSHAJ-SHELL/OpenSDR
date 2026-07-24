import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.core.crypto import encrypt
from craftsman.core.models import Mailbox
from craftsman.core.schemas import (
    DeliverabilityReport,
    DkimOut,
    DmarcOut,
    DnsRecordOut,
    MailboxCreate,
    MailboxOut,
    MailboxUpdate,
    WarmupOut,
    WarmupStepOut,
)
from craftsman.deliverability.dns_auth import (
    check_dkim,
    check_dmarc,
    check_spf,
    looks_like_primary_domain,
)
from craftsman.ingest.verify import domain_of
from craftsman.sender.warmup import effective_daily_limit, warmup_schedule

router = APIRouter(prefix="/mailboxes", tags=["mailboxes"])


@router.post("", response_model=MailboxOut, dependencies=[Depends(require_scope("admin"))])
def add_mailbox(payload: MailboxCreate, db: Session = Depends(get_db)):
    # per-org mailbox quota (M5.1c): NULL = unlimited (self-hoster default)
    from sqlalchemy import func

    from craftsman.core.models import Org
    from craftsman.core.tenancy import require_org_id

    org = db.get(Org, require_org_id())
    if org is not None and org.max_mailboxes is not None:
        count = db.scalar(select(func.count(Mailbox.id))) or 0
        if count >= org.max_mailboxes:
            raise HTTPException(
                409, f"org mailbox quota reached ({org.max_mailboxes})"
            )
    imap_host = (payload.imap_host or "").strip() or None
    box = Mailbox(
        email=payload.email,
        smtp_host=payload.smtp_host,
        smtp_port=payload.smtp_port,
        smtp_user=payload.smtp_user,
        smtp_pass_enc=encrypt(payload.smtp_password),
        imap_host=imap_host,
        imap_port=payload.imap_port if imap_host else None,
        imap_pass_enc=(
            encrypt(payload.imap_password or payload.smtp_password) if imap_host else None
        ),
        dkim_selector=(payload.dkim_selector or "").strip() or None,
        daily_limit=payload.daily_limit,
    )
    db.add(box)
    db.flush()
    return box


@router.patch(
    "/{mailbox_id}", response_model=MailboxOut, dependencies=[Depends(require_scope("admin"))]
)
def update_mailbox(
    mailbox_id: uuid.UUID, payload: MailboxUpdate, db: Session = Depends(get_db)
):
    box = db.get(Mailbox, mailbox_id)
    if box is None:
        raise HTTPException(404, "mailbox not found")

    if payload.smtp_host is not None:
        box.smtp_host = payload.smtp_host
    if payload.smtp_port is not None:
        box.smtp_port = payload.smtp_port
    if payload.smtp_user is not None:
        box.smtp_user = payload.smtp_user
    if payload.smtp_password is not None:
        box.smtp_pass_enc = encrypt(payload.smtp_password)
    if payload.dkim_selector is not None:
        box.dkim_selector = payload.dkim_selector.strip() or None
    if payload.daily_limit is not None:
        box.daily_limit = payload.daily_limit
    if payload.health is not None:
        box.health = payload.health

    if payload.clear_imap:
        box.imap_host = None
        box.imap_port = None
        box.imap_pass_enc = None
    else:
        if payload.imap_host is not None:
            host = payload.imap_host.strip() or None
            box.imap_host = host
            if host is None:
                box.imap_port = None
                box.imap_pass_enc = None
        if payload.imap_port is not None and box.imap_host:
            box.imap_port = payload.imap_port
        if payload.imap_password is not None and box.imap_host:
            box.imap_pass_enc = encrypt(payload.imap_password)

    db.add(box)
    db.flush()
    return box


@router.get("", response_model=list[MailboxOut], dependencies=[Depends(require_scope("read"))])
def list_mailboxes(db: Session = Depends(get_db)):
    return list(db.scalars(select(Mailbox)).all())


@router.get(
    "/{mailbox_id}/deliverability",
    response_model=DeliverabilityReport,
    dependencies=[Depends(require_scope("read"))],
)
def deliverability(mailbox_id: uuid.UUID, db: Session = Depends(get_db)):
    """Live SPF/DKIM/DMARC status for the mailbox's sending domain + its warmup ramp.
    Read-only: the DNS lookups run server-side on every call (per-record timeout in
    dns_auth), nothing is persisted."""
    box = db.get(Mailbox, mailbox_id)
    if box is None:
        raise HTTPException(404, "mailbox not found")

    domain = domain_of(box.email)
    spf = check_spf(domain)
    dmarc = check_dmarc(domain)
    dkim = check_dkim(domain, box.dkim_selector)

    return DeliverabilityReport(
        mailbox_id=box.id,
        email=box.email,
        domain=domain,
        primary_domain_warning=looks_like_primary_domain(domain),
        spf=DnsRecordOut(status=spf.status, record=spf.record, recommended=spf.recommended),
        dmarc=DmarcOut(
            status=dmarc.status, policy=dmarc.policy, record=dmarc.record,
            recommended=dmarc.recommended,
        ),
        dkim=DkimOut(status=dkim.status, selector=dkim.selector, record=dkim.record),
        warmup=WarmupOut(
            stage=box.warmup_stage,
            effective_cap=effective_daily_limit(box.daily_limit, box.warmup_stage),
            daily_limit=box.daily_limit,
            sent_today=box.sent_today,
            advances_per_day=1,
            schedule=[WarmupStepOut(**s) for s in warmup_schedule(box.daily_limit)],
        ),
    )
