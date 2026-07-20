"""Symmetric encryption for mailbox credentials at rest (Fernet)."""

from cryptography.fernet import Fernet

from craftsman.core.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().craftsman_secret_key
    if not key:
        raise RuntimeError(
            "CRAFTSMAN_SECRET_KEY is not set. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
