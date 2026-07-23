"""Unit tests for the auth primitives — pure logic, no DB, no app."""

from craftsman.api.auth import (
    TOKEN_PREFIX,
    generate_token,
    has_scope,
    hash_token,
    key_prefix,
)


def test_generate_token_format_and_uniqueness():
    a, b = generate_token(), generate_token()
    assert a.startswith(TOKEN_PREFIX) and b.startswith(TOKEN_PREFIX)
    assert a != b
    assert len(a) > 20  # csk_ + 32 url-safe bytes


def test_hash_is_deterministic_and_hides_token():
    token = generate_token()
    assert hash_token(token) == hash_token(token)
    assert token not in hash_token(token)
    assert len(hash_token(token)) == 64  # sha-256 hex


def test_different_tokens_hash_differently():
    assert hash_token(generate_token()) != hash_token(generate_token())


def test_key_prefix_is_short_and_not_authenticating():
    token = generate_token()
    assert key_prefix(token) == token[:12]
    assert len(key_prefix(token)) == 12


def test_scope_hierarchy_admin_implies_all():
    assert has_scope(["admin"], "read")
    assert has_scope(["admin"], "operate")
    assert has_scope(["admin"], "admin")


def test_scope_hierarchy_operate_implies_read_not_admin():
    assert has_scope(["operate"], "read")
    assert has_scope(["operate"], "operate")
    assert not has_scope(["operate"], "admin")


def test_scope_read_only():
    assert has_scope(["read"], "read")
    assert not has_scope(["read"], "operate")
    assert not has_scope(["read"], "admin")


def test_empty_scopes_grant_nothing():
    assert not has_scope([], "read")
    assert not has_scope([], "operate")
    assert not has_scope([], "admin")


def test_unknown_scopes_ignored():
    assert not has_scope(["bogus"], "read")
    assert has_scope(["bogus", "operate"], "read")
