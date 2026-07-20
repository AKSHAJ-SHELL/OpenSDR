from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.deps import get_db
from craftsman.core.crypto import encrypt
from craftsman.core.models import Mailbox
from craftsman.core.schemas import MailboxCreate, MailboxOut

router = APIRouter(prefix="/mailboxes", tags=["mailboxes"])


@router.post("", response_model=MailboxOut)
def add_mailbox(payload: MailboxCreate, db: Session = Depends(get_db)):
    box = Mailbox(
        email=payload.email,
        smtp_host=payload.smtp_host,
        smtp_port=payload.smtp_port,
        smtp_user=payload.smtp_user,
        smtp_pass_enc=encrypt(payload.smtp_password),
        imap_host=payload.imap_host,
        imap_port=payload.imap_port,
        imap_pass_enc=encrypt(payload.imap_password or payload.smtp_password),
        daily_limit=payload.daily_limit,
    )
    db.add(box)
    db.flush()
    return box


@router.get("", response_model=list[MailboxOut])
def list_mailboxes(db: Session = Depends(get_db)):
    return db.scalars(select(Mailbox)).all()
