"""Password hashing delegated to argon2-cffi; no custom password cryptography."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()


def password_hash(password: str) -> str:
    return _hasher.hash(password)


def password_verify(encoded: str, password: str) -> bool:
    try:
        return _hasher.verify(encoded, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
