"""API-key management. All endpoints require the ``admin`` scope."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.auth import generate_token, hash_token, key_prefix, require_scope
from craftsman.api.deps import get_db
from craftsman.core.models import ApiKey
from craftsman.core.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut

router = APIRouter(
    prefix="/keys",
    tags=["keys"],
    dependencies=[Depends(require_scope("admin"))],
)


@router.post("", response_model=ApiKeyCreated, status_code=201)
def create_key(payload: ApiKeyCreate, db: Session = Depends(get_db)):
    token = generate_token()
    key = ApiKey(
        name=payload.name,
        key_prefix=key_prefix(token),
        key_hash=hash_token(token),
        scopes=list(dict.fromkeys(payload.scopes)),  # de-dupe, preserve order
    )
    db.add(key)
    db.flush()
    return ApiKeyCreated(
        id=key.id,
        name=key.name,
        key_prefix=key.key_prefix,
        scopes=key.scopes,
        created_at=key.created_at,
        token=token,
    )


@router.get("", response_model=list[ApiKeyOut])
def list_keys(db: Session = Depends(get_db)):
    return list(db.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())).all())


@router.delete("/{key_id}", status_code=204)
def revoke_key(key_id: uuid.UUID, db: Session = Depends(get_db)):
    key = db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(404, "key not found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        db.add(key)
