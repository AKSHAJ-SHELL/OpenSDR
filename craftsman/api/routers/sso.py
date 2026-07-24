"""OIDC SSO endpoints (M5.1b) — the two deliberately unauthenticated routes.

`/auth/oidc/login` and `/auth/oidc/callback` join `/health`, `/u/{token}`, and
the Cal.com webhook on the unauth allowlist: a browser mid-login cannot hold an
API key. They are gated instead by the OIDC protocol itself — signed state
(HMAC, 10-min expiry), nonce binding, full id_token validation against the
IdP's JWKS — and they 503 until OIDC is configured (keyless-off). The browser
leaves the callback with only a 60-second, one-time login code; sessions are
minted server-side by the dashboard via the admin-scoped exchange endpoint.

User resolution runs UNSCOPED on the credential pair (issuer, subject) — the
same pattern as the unsubscribe token — then everything else happens inside
the resolved user's org.
"""

import logging
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from craftsman.api.deps import get_db
from craftsman.core.config import get_settings
from craftsman.core.models import AuditLog, User
from craftsman.core.tenancy import DEFAULT_ORG_ID, org_context, unscoped_context
from craftsman.sso.oidc import (
    OidcError,
    authorize_url,
    check_state,
    exchange_code,
    make_state,
    mint_login_code,
    oidc_enabled,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/oidc", tags=["sso"])


def _dashboard(path: str) -> str:
    return get_settings().dashboard_base_url.rstrip("/") + path


def _login_error(reason: str) -> RedirectResponse:
    # generic reasons only — never echo IdP details or claim values to the URL
    return RedirectResponse(_dashboard(f"/login?error={quote(reason)}"), status_code=302)


@router.get("/login")
def oidc_login():
    if not oidc_enabled():
        raise HTTPException(503, "SSO not configured (set OIDC_DISCOVERY_URL / client id+secret)")
    nonce = secrets.token_urlsafe(16)
    state = make_state(nonce)
    return RedirectResponse(authorize_url(state, nonce), status_code=302)


@router.get("/callback")
def oidc_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if not oidc_enabled():
        raise HTTPException(503, "SSO not configured")
    if error or not code or not state:
        return _login_error("sso_denied")
    nonce = check_state(state)
    if nonce is None:
        return _login_error("sso_state_invalid")
    try:
        claims = exchange_code(code, expected_nonce=nonce)
    except OidcError as e:
        log.warning("OIDC exchange failed: %s", e)
        return _login_error("sso_exchange_failed")

    issuer, sub = claims["iss"], claims["sub"]
    with unscoped_context():
        user = db.scalar(
            select(User).where(User.oidc_issuer == issuer, User.oidc_sub == sub)
        )
        if user is None:
            user = _link_or_provision(db, claims)
    if user is None:
        return _login_error("sso_unknown_user")
    if user.disabled_at is not None:
        return _login_error("sso_user_disabled")

    code_out = mint_login_code(user.id)
    return RedirectResponse(
        _dashboard(f"/auth/sso?code={quote(code_out)}"), status_code=302
    )


def _link_or_provision(db: Session, claims: dict) -> User | None:
    """First SSO login for this subject. Link to an existing user only when the
    IdP asserts a VERIFIED email that matches exactly one unlinked user across
    the install (two matches = ambiguous = refuse; unverified email = refuse —
    the classic cross-IdP takeover hole). Otherwise JIT-provision into the
    default org as viewer, only if the operator opted in."""
    issuer, sub = claims["iss"], claims["sub"]
    email = (claims.get("email") or "").lower()

    if email and claims.get("email_verified") is True:
        matches = db.scalars(
            select(User).where(User.email == email, User.oidc_sub.is_(None))
        ).all()
        if len(matches) == 1:
            user = matches[0]
            user.oidc_issuer, user.oidc_sub = issuer, sub
            db.add(user)
            with org_context(user.org_id):
                db.add(AuditLog(event="user_sso_linked", detail={
                    "user_id": str(user.id), "issuer": issuer,
                }))
            return user
        if len(matches) > 1:
            log.warning("SSO email %s matches users in multiple orgs — refusing to link", email)
            return None

    if not get_settings().oidc_auto_provision or not email:
        return None
    with org_context(DEFAULT_ORG_ID):
        user = User(email=email, role="viewer", oidc_issuer=issuer, oidc_sub=sub,
                    display_name=claims.get("name"))
        db.add(user)
        db.flush()
        db.add(AuditLog(event="user_sso_provisioned", detail={
            "user_id": str(user.id), "issuer": issuer, "role": "viewer",
        }))
    return user
