"""Org users & RBAC (M5.1b) + the auth endpoints the dashboard proxy consumes.

Users are org-scoped rows (tenancy guard applies — a dashboard only ever sees
its own org's users). Roles map to API scopes via core/rbac.py; the dashboard
proxy holds one admin key and down-enforces the session user's role using
`GET /auth/route-scopes`, while the API's own scope checks remain the backstop.

The credential/SSO exchange endpoints require the `admin` scope because they
are called server-side by the dashboard with its key — they are never exposed
to browsers directly, and a 401/403 there means a misconfigured deployment,
not a login failure.
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.auth import require_scope
from craftsman.api.deps import get_db
from craftsman.core.models import AuditLog, User
from craftsman.core.rbac import ROLE_SCOPE, hash_password, verify_password
from craftsman.core.schemas import (
    CredentialsIn,
    CredentialVerifyOut,
    SsoExchangeIn,
    SsoStatusOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from craftsman.sso.oidc import oidc_enabled, redeem_login_code

log = logging.getLogger(__name__)

router = APIRouter(tags=["users"])

# burned when the email doesn't resolve, so verify-credentials costs one scrypt
# either way and timing doesn't reveal which addresses have accounts
_DUMMY_HASH = hash_password("craftsman-timing-equalizer")


def _user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id,
        email=u.email,
        display_name=u.display_name,
        role=u.role,
        has_password=u.password_hash is not None,
        sso_linked=u.oidc_sub is not None,
        disabled_at=u.disabled_at,
        last_login_at=u.last_login_at,
        created_at=u.created_at,
    )


def _audit(db: Session, event: str, detail: dict) -> None:
    db.add(AuditLog(event=event, detail=detail))


@router.get(
    "/users", response_model=list[UserOut], dependencies=[Depends(require_scope("read"))]
)
def list_users(db: Session = Depends(get_db)):
    return [_user_out(u) for u in db.scalars(select(User).order_by(User.created_at)).all()]


@router.post(
    "/users",
    response_model=UserOut,
    status_code=201,
    dependencies=[Depends(require_scope("admin"))],
)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    email = payload.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "a user with this email already exists")
    user = User(
        email=email,
        display_name=payload.display_name,
        role=payload.role,
        password_hash=hash_password(payload.password) if payload.password else None,
    )
    db.add(user)
    db.flush()
    _audit(db, "user_created", {"user_id": str(user.id), "email": email, "role": user.role})
    return _user_out(user)


def _other_active_owners(db: Session, user: User) -> int:
    return len(
        db.scalars(
            select(User.id).where(
                User.role == "owner", User.disabled_at.is_(None), User.id != user.id
            )
        ).all()
    )


@router.patch(
    "/users/{user_id}",
    response_model=UserOut,
    dependencies=[Depends(require_scope("admin"))],
)
def update_user(user_id: uuid.UUID, payload: UserUpdate, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user not found")

    changes: dict = {}
    demoting = payload.role is not None and payload.role != "owner" and user.role == "owner"
    disabling = (
        payload.disabled is True and user.disabled_at is None and user.role == "owner"
    )
    if (demoting or disabling) and _other_active_owners(db, user) == 0:
        # lockout guard: an org must always keep one active owner
        raise HTTPException(409, "cannot remove the org's last active owner")

    if payload.role is not None and payload.role != user.role:
        changes["role"] = {"from": user.role, "to": payload.role}
        user.role = payload.role
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.disabled is True and user.disabled_at is None:
        user.disabled_at = datetime.now(timezone.utc)
        changes["disabled"] = True
    elif payload.disabled is False and user.disabled_at is not None:
        user.disabled_at = None
        changes["enabled"] = True
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
        changes["password"] = "reset"

    db.add(user)
    if changes:
        _audit(db, "user_updated", {"user_id": str(user.id), **changes})
    return _user_out(user)


# ─── dashboard-server auth endpoints ────────────────────────────────────────


@router.post(
    "/auth/verify-credentials",
    response_model=CredentialVerifyOut,
    dependencies=[Depends(require_scope("admin"))],
)
def verify_credentials(payload: CredentialsIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    ok = verify_password(
        payload.password, user.password_hash if user and user.password_hash else _DUMMY_HASH
    ) and user is not None and user.password_hash is not None
    if user is None or not ok or user.disabled_at is not None:
        raise HTTPException(401, "invalid credentials")
    user.last_login_at = datetime.now(timezone.utc)
    db.add(user)
    return CredentialVerifyOut(
        user_id=user.id, email=user.email, role=user.role, display_name=user.display_name
    )


@router.post(
    "/auth/sso/exchange",
    response_model=CredentialVerifyOut,
    dependencies=[Depends(require_scope("admin"))],
)
def sso_exchange(payload: SsoExchangeIn, db: Session = Depends(get_db)):
    """Redeem a one-time SSO login code for the user identity. Org-scoped: a
    code minted for another org's user resolves to nothing here — a dashboard
    can only ever establish sessions for its own org."""
    user_id = redeem_login_code(payload.code)
    if user_id is None:
        raise HTTPException(401, "invalid or expired login code")
    # explicit filtered SELECT, never identity-map get: this is the endpoint
    # that turns a code into a session, so the org check must hit SQL
    user = db.scalar(select(User).where(User.id == user_id))
    if user is None or user.disabled_at is not None:
        raise HTTPException(401, "unknown or disabled user")
    user.last_login_at = datetime.now(timezone.utc)
    db.add(user)
    return CredentialVerifyOut(
        user_id=user.id, email=user.email, role=user.role, display_name=user.display_name
    )


@router.get(
    "/auth/sso/status",
    response_model=SsoStatusOut,
    dependencies=[Depends(require_scope("read"))],
)
def sso_status():
    return SsoStatusOut(enabled=oidc_enabled())


def _iter_api_routes(router):
    """Every APIRoute, descending into included sub-routers — this FastAPI keeps
    include_router results as nested router objects (same recursion the M0.1
    fail-closed audit uses)."""
    from fastapi.routing import APIRoute

    for route in getattr(router, "routes", []):
        if isinstance(route, APIRoute):
            yield route
        sub = getattr(route, "original_router", None)
        if sub is not None:
            yield from _iter_api_routes(sub)


def _route_scope(route) -> str | None:
    for dep in getattr(route.dependant, "dependencies", []) or []:
        marker = getattr(dep.call, "__craftsman_required_scope__", None)
        if marker is not None:
            return marker
    for dep in getattr(route, "dependencies", []) or []:
        marker = getattr(
            getattr(dep, "dependency", None), "__craftsman_required_scope__", None
        )
        if marker is not None:
            return marker
    return None


@router.get("/auth/route-scopes", dependencies=[Depends(require_scope("read"))])
def route_scopes(request: Request) -> dict[str, str]:
    """`"METHOD /path" → scope` for every authenticated route, introspected from
    the same markers the fail-closed audit reads — the dashboard proxy uses this
    to down-enforce a session user's role without maintaining its own list."""
    out: dict[str, str] = {}
    for route in _iter_api_routes(request.app):
        scope = _route_scope(route)
        if scope is None:
            continue
        for method in route.methods or []:
            if method not in ("HEAD", "OPTIONS"):
                out[f"{method} {route.path}"] = scope
    return out


# re-exported so the proxy docs can reference one canonical mapping
ROLE_SCOPE_TABLE = ROLE_SCOPE
