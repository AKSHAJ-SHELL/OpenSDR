"""Generic OIDC SSO (M5.1b, ⛔ Gate M5 Q3: authlib approved).

Code flow lives in the API (not the dashboard proxy) so SSO also covers
API-first deployments; the dashboard consumes the same endpoints. Keyless-off:
an empty `OIDC_DISCOVERY_URL` disables everything (the M4.3 webhook pattern).

Flow: `/auth/oidc/login` → IdP → `/auth/oidc/callback` validates the code +
id_token, resolves the user by (issuer, subject) — the credential pair, looked
up unscoped — and redirects to the dashboard with a short-lived, one-time,
HMAC-signed **login code**. The dashboard's server exchanges that code
(`POST /auth/sso/exchange`, admin key, org-scoped) for the user identity and
mints its own session cookie. The browser never sees an API key or id_token.

State/nonce/login codes are all signed with CRAFTSMAN_SECRET_KEY; login codes
are additionally one-time via a Redis SETNX tombstone. Providers: anything
speaking discovery + code flow (Google/Okta/Entra configs in the README).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import redis
from authlib.jose import JsonWebToken

from craftsman.core.config import get_settings

log = logging.getLogger(__name__)

STATE_TTL_S = 600
LOGIN_CODE_TTL_S = 60

_ID_TOKEN_ALGS = ["RS256", "ES256", "PS256"]


class OidcError(RuntimeError):
    """Any failure in the SSO flow — always safe to show as a generic 4xx."""


def oidc_enabled() -> bool:
    s = get_settings()
    return bool(s.oidc_discovery_url and s.oidc_client_id and s.oidc_client_secret)


def _secret() -> bytes:
    key = get_settings().craftsman_secret_key
    if not key:
        raise OidcError("CRAFTSMAN_SECRET_KEY is required for SSO")
    return key.encode()


def _redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


# ─── signed blobs (state + login codes) ─────────────────────────────────────


def _sign_blob(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _verify_blob(blob: str) -> dict | None:
    try:
        body, sig = blob.rsplit(".", 1)
    except ValueError:
        return None
    expect = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, sig):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


def make_state(nonce: str) -> str:
    return _sign_blob({"k": "state", "n": nonce, "exp": time.time() + STATE_TTL_S})


def check_state(state: str) -> str | None:
    """Returns the nonce bound into the state, or None if invalid/expired."""
    payload = _verify_blob(state)
    if payload is None or payload.get("k") != "state":
        return None
    return payload.get("n")


def mint_login_code(user_id: uuid.UUID) -> str:
    return _sign_blob({
        "k": "login",
        "uid": str(user_id),
        "jti": secrets.token_urlsafe(16),
        "exp": time.time() + LOGIN_CODE_TTL_S,
    })


def redeem_login_code(code: str, r: redis.Redis | None = None) -> uuid.UUID | None:
    """Verify signature + expiry, then burn the jti — a code redeems exactly once."""
    payload = _verify_blob(code)
    if payload is None or payload.get("k") != "login":
        return None
    r = r or _redis()
    # SETNX tombstone: the second redeemer finds the key and is refused
    if not r.set(f"sso:jti:{payload['jti']}", "1", nx=True, ex=LOGIN_CODE_TTL_S * 2):
        return None
    try:
        return uuid.UUID(payload["uid"])
    except (KeyError, ValueError):
        return None


# ─── the provider conversation ──────────────────────────────────────────────


@dataclass(frozen=True)
class OidcProvider:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


_provider_cache: dict[str, OidcProvider] = {}
_jwks_cache: dict[str, dict] = {}


def discover(force: bool = False) -> OidcProvider:
    """Fetch (and cache) the discovery document. Network seam for tests."""
    url = get_settings().oidc_discovery_url
    if not force and url in _provider_cache:
        return _provider_cache[url]
    doc = _fetch_json(url)
    try:
        provider = OidcProvider(
            issuer=doc["issuer"],
            authorization_endpoint=doc["authorization_endpoint"],
            token_endpoint=doc["token_endpoint"],
            jwks_uri=doc["jwks_uri"],
        )
    except KeyError as e:
        raise OidcError(f"discovery document missing {e}")
    _provider_cache[url] = provider
    return provider


def _fetch_json(url: str) -> dict:
    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def authorize_url(state: str, nonce: str) -> str:
    s = get_settings()
    provider = discover()
    return provider.authorization_endpoint + "?" + urlencode({
        "response_type": "code",
        "client_id": s.oidc_client_id,
        "redirect_uri": s.oidc_redirect_url,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
    })


def _fetch_token(code: str) -> dict:
    """POST the token endpoint (client_secret_post). Network seam for tests."""
    s = get_settings()
    provider = discover()
    resp = httpx.post(
        provider.token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": s.oidc_redirect_url,
            "client_id": s.oidc_client_id,
            "client_secret": s.oidc_client_secret,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _jwks() -> dict:
    provider = discover()
    if provider.jwks_uri not in _jwks_cache:
        _jwks_cache[provider.jwks_uri] = _fetch_json(provider.jwks_uri)
    return _jwks_cache[provider.jwks_uri]


def exchange_code(code: str, expected_nonce: str) -> dict:
    """Code → validated id_token claims: signature (JWKS), iss, aud, exp, nonce.

    Returns the claims dict; raises OidcError on any validation failure."""
    s = get_settings()
    token_response = _fetch_token(code)
    raw_id_token = token_response.get("id_token")
    if not raw_id_token:
        raise OidcError("token response had no id_token")
    jwt = JsonWebToken(_ID_TOKEN_ALGS)
    try:
        claims = jwt.decode(
            raw_id_token,
            _jwks(),
            claims_options={
                "iss": {"essential": True, "value": discover().issuer},
                "aud": {"essential": True, "value": s.oidc_client_id},
                "exp": {"essential": True},
                "sub": {"essential": True},
            },
        )
        claims.validate()
    except Exception as e:
        raise OidcError(f"id_token validation failed: {e}")
    if claims.get("nonce") != expected_nonce:
        raise OidcError("nonce mismatch (possible replay)")
    return dict(claims)
