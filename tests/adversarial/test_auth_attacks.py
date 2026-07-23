"""Adversarial auth tests — predict-then-run (TESTING.md §3).

Each test states the predicted outcome in a comment before the assertion. Where
prediction and behavior would differ, that is a finding — not something to paper over.

Shared fixtures (client, make_key, db) come from tests/conftest.py.
"""

import base64
import uuid

READ = "/leads"  # a read-scoped GET to probe with


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_empty_bearer_value(client, make_key):
    make_key("read")
    # Predict: 401 — "Bearer" with no credentials is not a valid key.
    r = client.get(READ, headers={"Authorization": "Bearer "})
    assert r.status_code == 401


def test_bearer_scheme_only(client, make_key):
    make_key("read")
    # Predict: 401 — no credentials part at all.
    r = client.get(READ, headers={"Authorization": "Bearer"})
    assert r.status_code == 401


def test_valid_format_unknown_token(client, make_key):
    make_key("read")
    # Predict: 401 — well-formed csk_ token that was never issued.
    from craftsman.api.auth import generate_token

    r = client.get(READ, headers=_auth(generate_token()))
    assert r.status_code == 401


def test_wrong_prefix_token(client, make_key):
    make_key("read")
    # Predict: 401 — random junk with no csk_ prefix is not in the store.
    r = client.get(READ, headers=_auth("not-a-real-token-xxxxxxxx"))
    assert r.status_code == 401


def test_revoked_token_cannot_be_reused(client, make_key):
    # Predict: 401 — a revoked key is rejected even though its hash still exists.
    token = make_key("read", revoked=True)
    r = client.get(READ, headers=_auth(token))
    assert r.status_code == 401


def test_one_char_mutation_of_valid_token(client, make_key):
    token = make_key("read")
    mutated = token[:-1] + ("A" if token[-1] != "A" else "B")
    # Predict: 401 — the hash is of the exact token; one char off misses entirely.
    r = client.get(READ, headers=_auth(mutated))
    assert r.status_code == 401


def test_token_in_query_string_does_not_authenticate(client, make_key):
    token = make_key("read")
    # Predict: 401 — only the Authorization header is honored, never a query param.
    r = client.get(f"{READ}?token={token}")
    assert r.status_code == 401
    r2 = client.get(f"{READ}?access_token={token}")
    assert r2.status_code == 401


def test_basic_scheme_rejected(client, make_key):
    token = make_key("read")
    creds = base64.b64encode(f"x:{token}".encode()).decode()
    # Predict: 401 — a non-Bearer scheme is not accepted.
    r = client.get(READ, headers={"Authorization": f"Basic {creds}"})
    assert r.status_code == 401


def test_lowercase_bearer_scheme_is_accepted(client, make_key):
    token = make_key("read")
    # Predict: 200 — HTTP auth schemes are case-insensitive, so "bearer" is valid.
    r = client.get(READ, headers={"Authorization": f"bearer {token}"})
    assert r.status_code == 200


def test_oversized_token(client, make_key):
    make_key("read")
    # Predict: 401 — a 10k-char token is just an unknown token; no crash.
    r = client.get(READ, headers=_auth("csk_" + "a" * 10000))
    assert r.status_code == 401


def test_non_ascii_token(client, make_key):
    make_key("read")
    # HTTP headers are a byte transport (latin-1), so a non-ascii token arrives as
    # raw bytes. Predict: 401 — it hashes to something not in the store; no crash.
    r = client.get(READ, headers={"Authorization": "Bearer caf\xe9".encode("latin-1")})
    assert r.status_code == 401


def test_admin_route_with_no_key_is_401_not_403(client):
    # Predict: 401 (missing key), not 403 — absence of a key is unauthenticated,
    # not merely under-scoped.
    r = client.delete(f"/leads/{uuid.uuid4()}/erase")
    assert r.status_code == 401
