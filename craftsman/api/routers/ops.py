"""Operational visibility endpoints: dead letters (and, in Phase 4, re-drive actions)."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.core.models import DeadLetter
from craftsman.core.schemas import DeadLetterOut

router = APIRouter(tags=["ops"])


@router.get(
    "/dead-letters",
    response_model=list[DeadLetterOut],
    dependencies=[Depends(require_scope("read"))],
)
def list_dead_letters(limit: int = 50, db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(DeadLetter).order_by(DeadLetter.created_at.desc()).limit(limit)
        ).all()
    )
