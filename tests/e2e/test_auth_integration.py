"""Auth integration tests over the real app + Postgres.

Covers: every endpoint 401s without a key (fail-closed route walk), the
unauthenticated allowlist still works anonymously, the scope matrix, the
key lifecycle, and the gated /docs + /openapi.json.
"""

import uuid

from fastapi.routing import APIRoute

from craftsman.api.app import app

# The only paths intentionally reachable without a key.
#   /health   — liveness probes
#   /u/{token} — RFC 8058 requires an unauthenticated one-click unsubscribe POST
#   /meetings/webhooks/calcom — Cal.com can't hold an API key; gated by HMAC-SHA256
#     over the raw body instead, and 503-off until CALCOM_WEBHOOK_SECRET is set
UNAUTH_ALLOWLIST = {"/health", "/u/{token}", "/meetings/webhooks/calcom"}


def _concrete(path: str) -> str:
    """Fill path params with a throwaway value so the route resolves."""
    out = path
    while "{" in out:
        start = out.index("{")
        end = out.index("}")
        out = out[:start] + str(uuid.uuid4()) + out[end + 1 :]
    return out


def _iter_api_routes(router):
    """Yield every APIRoute, descending into included sub-routers.

    This FastAPI keeps `include_router` results as nested router objects rather
    than flattening them onto `app.routes`, so we recurse via `original_router`.
    """
    for route in getattr(router, "routes", []):
        if isinstance(route, APIRoute):
            yield route
        sub = getattr(route, "original_router", None)
        if sub is not None:
            yield from _iter_api_routes(sub)


def _authed_routes():
    for route in _iter_api_routes(app):
        if route.path in UNAUTH_ALLOWLIST:
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, route.path


def test_every_endpoint_rejects_missing_key(client):
    """Fail-closed: any route not on the allowlist must 401 without a key.

    A newly-added unauthenticated route breaks this test by construction.
    """
    checked = 0
    for method, path in _authed_routes():
        resp = client.request(method, _concrete(path))
        assert resp.status_code == 401, f"{method} {path} returned {resp.status_code}, expected 401"
        assert resp.headers.get("WWW-Authenticate") == "Bearer"
        checked += 1
    assert checked >= 15  # sanity: we actually walked the surface


def test_health_is_anonymous(client):
    assert client.get("/health").status_code == 200


def test_unsubscribe_is_anonymous(client):
    # unknown token → 404 from the handler, NOT 401 — proves auth didn't gate it
    token = "does-not-exist"
    assert client.get(f"/u/{token}").status_code == 404
    assert client.post(f"/u/{token}").status_code == 404


def test_read_key_allows_get_but_not_operate(client, make_key):
    token = make_key("read")
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/leads", headers=h).status_code == 200
    assert client.get("/analytics/overview", headers=h).status_code == 200
    # operate route with a read-only key → 403
    r = client.post(f"/campaigns/{uuid.uuid4()}/pause", headers=h)
    assert r.status_code == 403
    assert "operate" in r.json()["detail"]


def test_operate_key_allows_operate_but_not_admin(client, make_key):
    token = make_key("operate")
    h = {"Authorization": f"Bearer {token}"}
    # operate implies read
    assert client.get("/leads", headers=h).status_code == 200
    # pause an unknown campaign: passes auth, 404 from handler
    assert client.post(f"/campaigns/{uuid.uuid4()}/pause", headers=h).status_code == 404
    # admin route with operate key → 403
    r = client.delete(f"/leads/{uuid.uuid4()}/erase", headers=h)
    assert r.status_code == 403
    assert "admin" in r.json()["detail"]


def test_admin_key_passes_everywhere(client, make_key):
    token = make_key("admin")
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/mailboxes", headers=h).status_code == 200
    # admin erase of an unknown lead: passes auth, 404 from handler
    assert client.delete(f"/leads/{uuid.uuid4()}/erase", headers=h).status_code == 404
    assert client.get("/keys", headers=h).status_code == 200


def test_docs_and_openapi_require_key(client, make_key):
    assert client.get("/openapi.json").status_code == 401
    assert client.get("/docs").status_code == 401
    h = {"Authorization": f"Bearer {make_key('read')}"}
    spec = client.get("/openapi.json", headers=h)
    assert spec.status_code == 200 and spec.json()["info"]["title"] == "Craftsman"
    assert client.get("/docs", headers=h).status_code == 200


# ---------------------------------------------------------------- key lifecycle


def test_key_lifecycle_create_use_revoke(client, make_key):
    admin_h = {"Authorization": f"Bearer {make_key('admin')}"}

    created = client.post(
        "/keys", json={"name": "ci", "scopes": ["read"]}, headers=admin_h
    )
    assert created.status_code == 201
    body = created.json()
    token = body["token"]
    assert token.startswith("csk_")
    assert body["key_prefix"] == token[:12]
    assert "key_hash" not in body

    # the freshly-minted token authenticates
    new_h = {"Authorization": f"Bearer {token}"}
    assert client.get("/leads", headers=new_h).status_code == 200

    # listing never leaks the hash or token
    listed = client.get("/keys", headers=admin_h).json()
    assert all("key_hash" not in k and "token" not in k for k in listed)

    # revoke → the token stops working
    resp = client.delete(f"/keys/{body['id']}", headers=admin_h)
    assert resp.status_code == 204
    assert client.get("/leads", headers=new_h).status_code == 401


def test_create_key_rejects_unknown_scope(client, make_key):
    admin_h = {"Authorization": f"Bearer {make_key('admin')}"}
    resp = client.post(
        "/keys", json={"name": "bad", "scopes": ["superuser"]}, headers=admin_h
    )
    assert resp.status_code == 422


def test_keys_endpoints_require_admin(client, make_key):
    op_h = {"Authorization": f"Bearer {make_key('operate')}"}
    assert client.get("/keys", headers=op_h).status_code == 403
    assert client.post(
        "/keys", json={"name": "x", "scopes": ["read"]}, headers=op_h
    ).status_code == 403
