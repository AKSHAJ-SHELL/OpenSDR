"""RBAC role→scope map and the shared scrypt password format (M5.1b)."""

from craftsman.api.auth import has_scope
from craftsman.core.rbac import ROLE_SCOPE, ROLES, hash_password, verify_password


def test_role_scope_map_is_total_and_hierarchical():
    assert set(ROLE_SCOPE) == set(ROLES)
    # owner ⊃ operator ⊃ viewer via the scope hierarchy
    assert has_scope([ROLE_SCOPE["owner"]], "admin")
    assert has_scope([ROLE_SCOPE["operator"]], "operate")
    assert not has_scope([ROLE_SCOPE["operator"]], "admin")
    assert has_scope([ROLE_SCOPE["viewer"]], "read")
    assert not has_scope([ROLE_SCOPE["viewer"]], "operate")


def test_password_round_trip():
    h = hash_password("correct horse battery staple")
    assert h.startswith("scrypt$")
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)


def test_hashes_are_salted():
    assert hash_password("same") != hash_password("same")


def test_dashboard_hash_format_is_interchangeable():
    """The node dashboard writes scrypt$saltHex$hashHex with N=16384 r=8 p=1
    dklen=64 — a known vector computed with those exact parameters verifies."""
    import hashlib

    salt = bytes.fromhex("00112233445566778899aabbccddeeff")
    derived = hashlib.scrypt(
        b"hunter2hunter2", salt=salt, n=16384, r=8, p=1, dklen=64
    )
    stored = f"scrypt${salt.hex()}${derived.hex()}"
    assert verify_password("hunter2hunter2", stored)
    assert not verify_password("hunter3hunter3", stored)


def test_malformed_and_absent_hashes_never_verify_or_raise():
    for stored in (None, "", "scrypt$zz$zz", "bcrypt$x$y", "scrypt$deadbeef", "$$"):
        assert verify_password("anything", stored) is False
