"""Users, RBAC, and the SSO login flow end-to-end (M5.1b).

The IdP conversation itself (discovery/token/JWKS fetches) is stubbed at the
module seams — what is under test is everything Craftsman does around it:
state handling, user resolution, provisioning policy, login-code exchange,
role mapping, and the lockout guard.
"""

import uuid

import fakeredis
import pytest
from sqlalchemy import select

from craftsman.core.models import AuditLog, Org, User
from craftsman.core.rbac import hash_password
from craftsman.core.tenancy import org_context, unscoped_context
from craftsman.sso import oidc as sso_mod


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ─── users CRUD + roles ─────────────────────────────────────────────────────


def test_user_crud_and_scopes(client, make_key, db):
    admin, read = make_key("admin"), make_key("read")

    r = client.post("/users", headers=_auth(read), json={"email": "a@example.com"})
    assert r.status_code == 403  # viewer-scope keys cannot manage users

    r = client.post(
        "/users",
        headers=_auth(admin),
        json={"email": "owner@example.com", "role": "owner", "password": "a-long-password-1"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["role"] == "owner" and body["has_password"] is True

    r = client.post("/users", headers=_auth(admin), json={"email": "owner@example.com"})
    assert r.status_code == 409  # duplicate email in org

    r = client.get("/users", headers=_auth(read))
    assert r.status_code == 200 and len(r.json()) == 1

    audit = db.scalars(select(AuditLog).where(AuditLog.event == "user_created")).all()
    assert len(audit) == 1


def test_last_owner_lockout_guard(client, make_key):
    admin = make_key("admin")
    r = client.post(
        "/users", headers=_auth(admin),
        json={"email": "solo@example.com", "role": "owner", "password": "a-long-password-1"},
    )
    uid = r.json()["id"]

    # the only active owner can be neither demoted nor disabled
    assert client.patch(
        f"/users/{uid}", headers=_auth(admin), json={"role": "viewer"}
    ).status_code == 409
    assert client.patch(
        f"/users/{uid}", headers=_auth(admin), json={"disabled": True}
    ).status_code == 409

    # with a second owner present, both operations go through
    client.post(
        "/users", headers=_auth(admin),
        json={"email": "second@example.com", "role": "owner", "password": "a-long-password-2"},
    )
    assert client.patch(
        f"/users/{uid}", headers=_auth(admin), json={"role": "viewer"}
    ).status_code == 200


def test_verify_credentials(client, make_key):
    admin = make_key("admin")
    client.post(
        "/users", headers=_auth(admin),
        json={"email": "dana@example.com", "role": "operator", "password": "a-long-password-1"},
    )

    ok = client.post(
        "/auth/verify-credentials", headers=_auth(admin),
        json={"email": "dana@example.com", "password": "a-long-password-1"},
    )
    assert ok.status_code == 200
    assert ok.json()["role"] == "operator"

    for email, pw in [
        ("dana@example.com", "wrong-password-000"),  # wrong password
        ("ghost@example.com", "a-long-password-1"),  # unknown user
    ]:
        r = client.post(
            "/auth/verify-credentials", headers=_auth(admin),
            json={"email": email, "password": pw},
        )
        assert r.status_code == 401


def test_disabled_and_sso_only_users_cannot_password_login(client, make_key):
    admin = make_key("admin")
    r = client.post(
        "/users", headers=_auth(admin),
        json={"email": "off@example.com", "role": "operator", "password": "a-long-password-1"},
    )
    uid = r.json()["id"]
    # need a second... no: disabling an operator needs no owner guard
    client.patch(f"/users/{uid}", headers=_auth(admin), json={"disabled": True})
    assert client.post(
        "/auth/verify-credentials", headers=_auth(admin),
        json={"email": "off@example.com", "password": "a-long-password-1"},
    ).status_code == 401

    client.post("/users", headers=_auth(admin), json={"email": "sso@example.com"})  # no password
    assert client.post(
        "/auth/verify-credentials", headers=_auth(admin),
        json={"email": "sso@example.com", "password": "anything-at-all-12"},
    ).status_code == 401


def test_route_scopes_map_covers_admin_routes(client, make_key):
    r = client.get("/auth/route-scopes", headers=_auth(make_key("read")))
    assert r.status_code == 200
    scopes = r.json()
    assert scopes.get("POST /users") == "admin"
    assert scopes.get("GET /users") == "read"
    assert scopes.get("DELETE /leads/{lead_id}/erase") == "admin"
    # unauthenticated routes never appear
    assert not any(p.endswith("/health") for p in scopes)


# ─── SSO flow ───────────────────────────────────────────────────────────────


@pytest.fixture()
def sso_env(monkeypatch):
    monkeypatch.setenv("OIDC_DISCOVERY_URL", "https://idp.test/.well-known/openid-configuration")
    monkeypatch.setenv("OIDC_CLIENT_ID", "craftsman-client")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("CRAFTSMAN_SECRET_KEY", "test-secret-key")
    from craftsman.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        sso_mod, "discover",
        lambda force=False: sso_mod.OidcProvider(
            issuer="https://idp.test",
            authorization_endpoint="https://idp.test/authorize",
            token_endpoint="https://idp.test/token",
            jwks_uri="https://idp.test/jwks",
        ),
    )
    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(sso_mod, "_redis", lambda: fake)
    yield
    get_settings.cache_clear()


def _stub_exchange(monkeypatch, claims):
    def fake_exchange(code, expected_nonce):
        return claims

    monkeypatch.setattr("craftsman.api.routers.sso.exchange_code", fake_exchange)


def test_sso_disabled_is_503(client):
    assert client.get("/auth/oidc/login", follow_redirects=False).status_code == 503


def test_sso_login_redirects_to_idp(client, sso_env):
    r = client.get("/auth/oidc/login", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://idp.test/authorize?")
    assert "state=" in r.headers["location"] and "nonce=" in r.headers["location"]


def test_sso_callback_full_login(client, make_key, db, monkeypatch, sso_env):
    admin = make_key("admin")
    client.post(
        "/users", headers=_auth(admin),
        json={"email": "dana@example.com", "role": "operator"},
    )
    _stub_exchange(monkeypatch, {
        "iss": "https://idp.test", "sub": "sub-123",
        "email": "dana@example.com", "email_verified": True,
    })
    state = sso_mod.make_state("n")
    r = client.get(
        f"/auth/oidc/callback?code=authcode&state={state}", follow_redirects=False
    )
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("http://localhost:3000/auth/sso?code=")
    login_code = loc.split("code=", 1)[1]

    from urllib.parse import unquote

    ex = client.post(
        "/auth/sso/exchange", headers=_auth(admin), json={"code": unquote(login_code)}
    )
    assert ex.status_code == 200
    assert ex.json()["email"] == "dana@example.com" and ex.json()["role"] == "operator"

    # verified-email first login linked the subject
    user = db.scalar(select(User).where(User.email == "dana@example.com"))
    assert user.oidc_sub == "sub-123"

    # login codes are one-time: replay is refused
    ex2 = client.post(
        "/auth/sso/exchange", headers=_auth(admin), json={"code": unquote(login_code)}
    )
    assert ex2.status_code == 401


def test_sso_unknown_subject_without_provisioning(client, monkeypatch, sso_env):
    _stub_exchange(monkeypatch, {
        "iss": "https://idp.test", "sub": "nobody",
        "email": "nobody@example.com", "email_verified": True,
    })
    state = sso_mod.make_state("n")
    r = client.get(
        f"/auth/oidc/callback?code=c&state={state}", follow_redirects=False
    )
    assert r.status_code == 302
    assert "error=sso_unknown_user" in r.headers["location"]


def test_sso_auto_provision_creates_viewer_in_default_org(
    client, db, monkeypatch, sso_env
):
    monkeypatch.setenv("OIDC_AUTO_PROVISION", "true")
    from craftsman.core.config import get_settings

    get_settings.cache_clear()
    _stub_exchange(monkeypatch, {
        "iss": "https://idp.test", "sub": "fresh-1",
        "email": "fresh@example.com", "email_verified": True, "name": "Fresh User",
    })
    state = sso_mod.make_state("n")
    r = client.get(f"/auth/oidc/callback?code=c&state={state}", follow_redirects=False)
    assert r.status_code == 302 and "/auth/sso?code=" in r.headers["location"]
    user = db.scalar(select(User).where(User.email == "fresh@example.com"))
    assert user is not None and user.role == "viewer"


def test_sso_tampered_state_rejected(client, monkeypatch, sso_env):
    _stub_exchange(monkeypatch, {"iss": "https://idp.test", "sub": "s"})
    r = client.get(
        "/auth/oidc/callback?code=c&state=forged.deadbeef", follow_redirects=False
    )
    assert r.status_code == 302
    assert "error=sso_state_invalid" in r.headers["location"]


def test_sso_unverified_email_never_links(client, make_key, db, monkeypatch, sso_env):
    """The cross-IdP takeover hole: an IdP asserting an UNVERIFIED email that
    matches an existing user must not be linked to that user's account."""
    admin = make_key("admin")
    client.post(
        "/users", headers=_auth(admin),
        json={"email": "victim@example.com", "role": "owner", "password": "a-long-password-1"},
    )
    _stub_exchange(monkeypatch, {
        "iss": "https://idp.test", "sub": "attacker-sub",
        "email": "victim@example.com", "email_verified": False,
    })
    state = sso_mod.make_state("n")
    r = client.get(f"/auth/oidc/callback?code=c&state={state}", follow_redirects=False)
    assert "error=sso_unknown_user" in r.headers["location"]
    user = db.scalar(select(User).where(User.email == "victim@example.com"))
    assert user.oidc_sub is None


def test_sso_exchange_is_org_scoped(client, make_key, db, monkeypatch, sso_env):
    """A login code minted for another org's user is worthless to this org's
    dashboard — the exchange resolves users inside the caller's org only."""
    admin = make_key("admin")
    with unscoped_context():
        other = Org(name="Other", slug=f"other-{uuid.uuid4().hex[:6]}")
        db.add(other)
        db.flush()
    with org_context(other.id):
        foreign = User(
            email="foreign@example.org", role="owner",
            password_hash=hash_password("a-long-password-9"),
        )
        db.add(foreign)
        db.flush()
    code = sso_mod.mint_login_code(foreign.id)
    r = client.post("/auth/sso/exchange", headers=_auth(admin), json={"code": code})
    assert r.status_code == 401
