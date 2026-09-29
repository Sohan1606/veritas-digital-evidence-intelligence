"""Authentication and authorization boundary.

Passwords use the maintained Argon2 implementation. Browser sessions are opaque random
credentials backed by revocable database records; only one-way SHA-256 digests are stored.
Authorization is capability-based and case-scope checks are performed in the database.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AccessDeniedError, AuthenticationUnavailableError
from app.core.security_passwords import password_hash, password_verify
from app.db.session import get_session
from app.db.types import utcnow
from app.domain.models import Case, OrganizationMembership, Role, RoleAssignment, User, UserSession
from app.domain.roles import ROLE_CAPABILITIES

SESSION_COOKIE = "veritas_session"
CSRF_COOKIE = "veritas_csrf"
CSRF_HEADER = "X-CSRF-Token"

PrincipalKind = Literal["user", "demonstration_viewer"]


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    kind: PrincipalKind
    authenticated: bool
    user_id: UUID | None = None
    session_id: str | None = None
    csrf_hash: str | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)


DEMONSTRATION_VIEWER = Principal(
    subject="system:demonstration-viewer",
    kind="demonstration_viewer",
    authenticated=False,
    permissions=frozenset({"demonstration:read"}),
)


def hash_password(password: str) -> str:
    """Delegate password hashing to the established Argon2id implementation."""
    return password_hash(password)


def verify_password(password_hash_value: str, password: str) -> bool:
    return password_verify(password_hash_value, password)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def optional_principal(request: Request, session: Session) -> Principal | None:
    """Resolve an existing session without requiring sign-in (session introspection)."""
    settings: Settings = request.app.state.settings
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return DEMONSTRATION_VIEWER if settings.access_mode == "demo" else None
    stored = session.execute(
        select(UserSession).where(UserSession.token_hash == _digest(token))
    ).scalar_one_or_none()
    now = utcnow()
    if stored is None or stored.revoked_at is not None:
        return None
    if stored.expires_at <= now:
        stored.revoked_at = now
        from app.services.records import organization_id_for_user, record_security_event

        record_security_event(
            session,
            actor="system:authentication",
            action="session.invalidated",
            entity_type="session",
            entity_public_id=stored.public_id,
            details={"reason": "expired"},
            organization_id=organization_id_for_user(session, stored.user_id),
        )
        session.commit()
        return None
    user = session.execute(select(User).where(User.id == stored.user_id)).scalar_one_or_none()
    if user is None or user.status != "active":
        stored.revoked_at = now
        from app.services.records import organization_id_for_user, record_security_event

        record_security_event(
            session,
            actor="system:authentication",
            action="session.invalidated",
            entity_type="session",
            entity_public_id=stored.public_id,
            details={"reason": "user_disabled"},
            organization_id=organization_id_for_user(session, stored.user_id),
        )
        session.commit()
        return None
    return Principal(
        subject=user.public_id,
        kind="user",
        authenticated=True,
        user_id=user.id,
        session_id=stored.public_id,
        csrf_hash=stored.csrf_hash,
        permissions=frozenset(),
    )


def get_optional_principal(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> Principal | None:
    return optional_principal(request, session)


def get_principal(request: Request, session: Annotated[Session, Depends(get_session)]) -> Principal:
    principal = optional_principal(request, session)
    if principal is None:
        raise AuthenticationUnavailableError("Authentication is required")
    return principal


def has_capability(session: Session, principal: Principal, capability: str) -> bool:
    if not principal.authenticated or principal.user_id is None:
        return False
    rows: Sequence[Any] = (
        session.execute(
            select(Role.name)
            .join(RoleAssignment, RoleAssignment.role_id == Role.id)
            .join(OrganizationMembership, OrganizationMembership.id == RoleAssignment.membership_id)
            .where(
                OrganizationMembership.user_id == principal.user_id,
                OrganizationMembership.status == "active",
                RoleAssignment.case_id.is_(None),
            )
        )
        .scalars()
        .all()
    )
    return any(capability in ROLE_CAPABILITIES.get(role, frozenset()) for role in rows)


def organization_ids_with_capability(
    session: Session, principal: Principal, capability: str
) -> set[UUID]:
    """Organization scopes in which this User has the requested unscoped capability."""
    if not principal.authenticated or principal.user_id is None:
        return set()
    rows = session.execute(
        select(Role.name, OrganizationMembership.organization_id)
        .join(RoleAssignment, RoleAssignment.role_id == Role.id)
        .join(OrganizationMembership, OrganizationMembership.id == RoleAssignment.membership_id)
        .where(
            OrganizationMembership.user_id == principal.user_id,
            OrganizationMembership.status == "active",
            RoleAssignment.case_id.is_(None),
        )
    ).all()
    return {
        organization_id
        for role_name, organization_id in rows
        if capability in ROLE_CAPABILITIES.get(role_name, frozenset())
    }


def has_case_capability(
    session: Session, principal: Principal, case: Case, capability: str
) -> bool:
    if principal.kind == "demonstration_viewer":
        demo_read = {
            "case:read",
            "evidence:read",
            "findings:read",
            "claims:read",
            "graph:read",
            "review:read",
            "case_audit:read",
            "examination:read",
        }
        return case.is_demonstration and capability in demo_read
    if not principal.authenticated or principal.user_id is None:
        return False
    assignments = session.execute(
        select(Role.name, RoleAssignment.case_id)
        .join(RoleAssignment, RoleAssignment.role_id == Role.id)
        .join(OrganizationMembership, OrganizationMembership.id == RoleAssignment.membership_id)
        .where(
            OrganizationMembership.user_id == principal.user_id,
            OrganizationMembership.organization_id == case.organization_id,
            OrganizationMembership.status == "active",
        )
    ).all()
    for role_name, case_id in assignments:
        allowed = ROLE_CAPABILITIES.get(role_name, frozenset())
        if case_id == case.id and capability in allowed:
            return True
        if case_id is None and "case:read:any" in allowed and capability in allowed:
            return True
    return False


def require_capability(capability: str) -> Callable[..., Principal]:
    def _dependency(
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Principal:
        if not has_capability(session, principal, capability):
            from app.services.records import record_security_event

            record_security_event(
                session,
                actor=principal.subject,
                action="authorization.denied",
                entity_type="capability",
                details={"capability": capability},
            )
            session.commit()
            raise AccessDeniedError("This action is not permitted")
        check_csrf(request, principal, session)
        return principal

    return _dependency


def check_csrf(request: Request, principal: Principal, session: Session) -> None:
    """Require a session-bound CSRF token for state-changing authenticated requests."""
    if request.method in {"GET", "HEAD", "OPTIONS"} or principal.kind != "user":
        return
    supplied = request.headers.get(CSRF_HEADER, "")
    cookie_value = request.cookies.get(CSRF_COOKIE, "")
    if (
        not principal.csrf_hash
        or not supplied
        or not cookie_value
        or not hmac.compare_digest(supplied, cookie_value)
        or not hmac.compare_digest(_digest(supplied), principal.csrf_hash)
    ):
        from app.services.records import record_security_event

        record_security_event(
            session,
            actor=principal.subject,
            action="authorization.denied",
            entity_type="session",
            entity_public_id=principal.session_id,
            details={"reason": "csrf_validation"},
        )
        session.commit()
        raise AccessDeniedError("A valid session-bound CSRF token is required")


def new_session_credentials() -> tuple[str, str, str, str]:
    token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    return token, _digest(token), _digest(csrf_token), csrf_token


def session_expiry(settings: Settings) -> datetime:
    return utcnow() + timedelta(minutes=settings.session_ttl_minutes)


def require_case_capability(capability: str) -> Callable[..., None]:
    def _dependency(
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> None:
        from app.core.errors import NotFoundError
        from app.services.queries import get_readable_case
        from app.services.records import record_security_event

        case_id = request.path_params.get("case_id")
        if not isinstance(case_id, str):
            raise NotFoundError("Resource was not found")
        case = get_readable_case(session, principal, case_id)
        if has_case_capability(session, principal, case, capability):
            return None
        record_security_event(
            session,
            actor=principal.subject,
            action="authorization.denied",
            entity_type="case",
            entity_public_id=case.public_id,
            details={"capability": capability},
        )
        session.commit()
        raise NotFoundError("Resource was not found")

    return _dependency
