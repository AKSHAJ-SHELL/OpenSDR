import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.deps import get_db
from craftsman.core.crypto import encrypt
from craftsman.core.models import Mailbox
from craftsman.core.schemas import MailboxCreate, MailboxOut, MailboxUpdate

router = APIRouter(prefix="/mailboxes", tags=["mailboxes"])


@router.post("", response_model=MailboxOut)
def add_mailbox(payload: MailboxCreate, db: Session = Depends(get_db)):
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
        daily_limit=payload.daily_limit,
    )
    db.add(box)
    db.flush()
    return box


@router.patch("/{mailbox_id}", response_model=MailboxOut)
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


@router.get("", response_model=list[MailboxOut])
def list_mailboxes(db: Session = Depends(get_db)):
    return list(db.scalars(select(Mailbox)).all())
