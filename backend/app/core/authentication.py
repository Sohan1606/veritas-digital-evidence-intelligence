"""Replaceable authentication adapter boundary.

The domain and authorization layer consume the stable User identity. A provider-specific
adapter can later verify an OIDC/MFA assertion and resolve its immutable issuer/subject to
that User without changing case policy or domain services.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security_passwords import password_hash, password_verify
from app.domain.models import User


class Authenticator(Protocol):
    def authenticate(self, session: Session, username: str, password: str) -> User | None: ...


class ProvisionedPasswordAuthenticator:
    """Local account adapter for explicitly provisioned users; public signup is absent."""

    def __init__(self) -> None:
        from secrets import token_urlsafe

        self._dummy_hash = password_hash(token_urlsafe(40))

    def authenticate(self, session: Session, username: str, password: str) -> User | None:
        normalized = username.strip().casefold()
        user = session.execute(select(User).where(User.username == normalized)).scalar_one_or_none()
        stored_hash = (
            user.password_hash if user is not None and user.status == "active" else self._dummy_hash
        )
        valid = password_verify(stored_hash, password)
        return user if user is not None and user.status == "active" and valid else None


def hash_provisioned_password(password: str) -> str:
    return password_hash(password)
