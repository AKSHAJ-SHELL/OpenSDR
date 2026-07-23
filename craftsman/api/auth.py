"""API-key authentication and scope enforcement.

Tokens are high-entropy random strings (`csk_` + 32 url-safe bytes). Only the
SHA-256 digest is stored; the plaintext is shown once at creation. Because the
token is random, a plain digest is enough — brute-forcing it is infeasible and
lookup stays O(1) by hash (the standard API-key pattern).

Scopes are hierarchical: ``admin`` implies ``operate`` implies ``read``.
"""

import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.deps import get_db
from craftsman.core.models import ApiKey

TOKEN_PREFIX = "csk_"
SCOPES: tuple[str, ...] = ("read", "operate", "admin")
_RANK = {"read": 0, "operate": 1, "admin": 2}

# HTTPBearer documents the scheme in /docs and, with auto_error=False, yields
# None for a missing or non-Bearer Authorization header (we raise 401 ourselves).
_bearer = HTTPBearer(auto_error=False, description="API key as: Authorization: Bearer <token>")


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def key_prefix(token: str) -> str:
    """The identifying prefix stored for display (never enough to authenticate)."""
    return token[:12]


def _max_rank(scopes: list[str]) -> int:
    return max((_RANK[s] for s in scopes if s in _RANK), default=-1)


def has_scope(granted: list[str], required: str) -> bool:
    return _max_rank(granted) >= _RANK[required]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_scope(scope: str):
    """FastAPI dependency factory: authenticate the bearer token and require ``scope``.

    Returns the authenticated ``ApiKey``. Raises 401 for a missing/unknown/revoked
    key and 403 for a valid key that lacks the required scope.
    """
    if scope not in _RANK:
        raise ValueError(f"unknown scope: {scope!r}")

    def dependency(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
        db: Session = Depends(get_db),
    ) -> ApiKey:
        if credentials is None or not credentials.credentials:
            raise _unauthorized("missing API key")
        api_key = db.scalar(
            select(ApiKey).where(ApiKey.key_hash == hash_token(credentials.credentials))
        )
        if api_key is None or api_key.revoked_at is not None:
            raise _unauthorized("invalid or revoked API key")
        if not has_scope(list(api_key.scopes), scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"this key lacks the required '{scope}' scope",
            )
        api_key.last_used_at = datetime.now(timezone.utc)
        db.add(api_key)
        return api_key

    # marker so a fail-closed route audit can recognise an authenticated route
    dependency.__craftsman_required_scope__ = scope  # type: ignore[attr-defined]
    return dependency
