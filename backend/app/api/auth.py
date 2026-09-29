"""Provisioned-identity sign-in and server-side session lifecycle."""

from __future__ import annotations

import hashlib
import secrets
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AuthenticationFailedError
from app.core.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    Principal,
    check_csrf,
    get_optional_principal,
    new_session_credentials,
    session_expiry,
)
from app.db.session import get_session
from app.db.types import utcnow
from app.domain.models import (
    LoginAttempt,
    Organization,
    OrganizationMembership,
    Role,
    RoleAssignment,
    User,
    UserSession,
)
from app.domain.roles import ROLE_CAPABILITIES
from app.schemas import LoginIn, SessionOut
from app.services.records import organization_id_for_user, record_security_event

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
SessionDep = Annotated[Session, Depends(get_session)]


def _login_bucket(ip: str) -> str:
    """One failure bucket per source IP prevents spraying usernames to bypass limits."""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()


def _roles_and_capabilities(
    session: Session, user: User
) -> tuple[list[str], set[str], list[str], dict[str, list[str]]]:
    from app.domain.models import Case

    rows = session.execute(
        select(Role.name, Organization.public_id, Case.public_id)
        .join(RoleAssignment, RoleAssignment.role_id == Role.id)
        .join(OrganizationMembership, OrganizationMembership.id == RoleAssignment.membership_id)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .outerjoin(Case, Case.id == RoleAssignment.case_id)
        .where(OrganizationMembership.user_id == user.id, OrganizationMembership.status == "active")
        .order_by(Role.name)
    ).all()
    roles = sorted({role for role, _org, _case in rows})
    capabilities = (
        set().union(*(ROLE_CAPABILITIES.get(role, frozenset()) for role, _org, _case in rows))
        if rows
        else set()
    )
    organizations = sorted({org for _role, org, _case in rows})
    scoped: dict[str, set[str]] = {}
    for role, _org, case_id in rows:
        role_caps = ROLE_CAPABILITIES.get(role, frozenset())
        if case_id is None:
            if "case:read:any" in role_caps:
                scoped.setdefault("*", set()).update(role_caps)
            continue
        scoped.setdefault(case_id, set()).update(role_caps)
    return (
        roles,
        capabilities,
        organizations,
        {key: sorted(values) for key, values in scoped.items()},
    )


def _session_out(session: Session, principal: Principal) -> SessionOut:
    if principal.kind == "demonstration_viewer":
        return SessionOut(
            authenticated=False,
            user_id=None,
            display_name=None,
            organization_ids=[],
            roles=[],
            capabilities=[
                "case:read",
                "evidence:read",
                "findings:read",
                "claims:read",
                "graph:read",
                "review:read",
                "case_audit:read",
                "examination:read",
            ],
            case_capabilities={
                "*": [
                    "case:read",
                    "evidence:read",
                    "findings:read",
                    "claims:read",
                    "graph:read",
                    "case_audit:read",
                    "examination:read",
                ]
            },
            session_id=None,
            expires_at=None,
            demonstration=True,
        )
    user = session.execute(select(User).where(User.public_id == principal.subject)).scalar_one()
    roles, capabilities, organizations, case_capabilities = _roles_and_capabilities(session, user)
    stored = session.execute(
        select(UserSession).where(UserSession.public_id == principal.session_id)
    ).scalar_one()
    return SessionOut(
        authenticated=True,
        user_id=user.public_id,
        display_name=user.display_name,
        organization_ids=organizations,
        roles=roles,
        capabilities=sorted(capabilities),
        case_capabilities=case_capabilities,
        session_id=stored.public_id,
        expires_at=stored.expires_at,
    )


def _cookie(response: Response, settings: Settings, token: str, expires: datetime) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_minutes * 60,
        expires=expires,
        httponly=True,
        secure=settings.environment == "production",
        samesite="strict",
        path="/",
    )


@router.post("/login", response_model=SessionOut)
def login(body: LoginIn, request: Request, response: Response, session: SessionDep) -> SessionOut:
    settings: Settings = request.app.state.settings
    ip = request.client.host if request.client else "unknown"
    if ip != "unknown":
        import ipaddress

        with suppress(ValueError):
            remote = ipaddress.ip_address(ip)
            trusted = any(
                remote in ipaddress.ip_network(network, strict=False)
                for network in settings.trusted_proxy_ips
            )
            forwarded = request.headers.get("x-real-ip", "")
            if trusted:
                with suppress(ValueError):
                    ip = str(ipaddress.ip_address(forwarded))
    # Expire old rate-limit buckets; do not retain source metadata indefinitely.
    session.execute(
        delete(LoginAttempt).where(LoginAttempt.updated_at < utcnow() - timedelta(days=1))
    )
    bucket = _login_bucket(ip)
    attempt = session.get(LoginAttempt, bucket)
    now = utcnow()
    locked = bool(attempt and attempt.locked_until and attempt.locked_until > now)
    user = request.app.state.authenticator.authenticate(session, body.username, body.password)
    usable_user = user is not None
    password_matches = usable_user

    if locked or not usable_user or not password_matches:
        if attempt is None:
            attempt = LoginAttempt(bucket=bucket, failures=0, window_started_at=now)
            session.add(attempt)
        if now - attempt.window_started_at > timedelta(minutes=settings.login_lockout_minutes):
            attempt.failures = 0
            attempt.window_started_at = now
            attempt.locked_until = None
        attempt.failures += 1
        if attempt.failures >= settings.login_failure_limit:
            attempt.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
        attempt.updated_at = now
        attempted_user = (
            user
            or session.execute(
                select(User).where(User.username == body.username.strip().casefold())
            ).scalar_one_or_none()
        )
        record_security_event(
            session,
            actor="system:authentication",
            action="authentication.failure",
            entity_type="user",
            entity_public_id=attempted_user.public_id if attempted_user else None,
            details={"result": "failure"},
            organization_id=(
                organization_id_for_user(session, attempted_user.id) if attempted_user else None
            ),
        )
        session.commit()
        raise AuthenticationFailedError("Invalid username or password")

    if attempt is not None:
        session.delete(attempt)
    token, token_hash, csrf_hash, csrf_token = new_session_credentials()
    expiry = session_expiry(settings)
    if user is None:
        raise AuthenticationFailedError("Invalid username or password")
    session_record = UserSession(
        user_id=user.id,
        token_hash=token_hash,
        csrf_hash=csrf_hash,
        expires_at=expiry,
        revoked_at=None,
        created_by=user.public_id,
    )
    session.add(session_record)
    session.flush()
    record_security_event(
        session,
        actor=user.public_id,
        action="authentication.success",
        entity_type="session",
        entity_public_id=session_record.public_id,
        details={"result": "success"},
    )
    session.commit()
    principal = Principal(
        subject=user.public_id,
        kind="user",
        authenticated=True,
        user_id=user.id,
        session_id=session_record.public_id,
        csrf_hash=csrf_hash,
    )
    _cookie(response, settings, token, expiry)
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=settings.session_ttl_minutes * 60,
        expires=expiry,
        httponly=False,
        secure=settings.environment == "production",
        samesite="strict",
        path="/",
    )
    return _session_out(session, principal)


@router.get("/session", response_model=SessionOut)
def session_state(
    request: Request,
    response: Response,
    session: SessionDep,
    principal: Annotated[Principal | None, Depends(get_optional_principal)],
) -> SessionOut:
    settings: Settings = request.app.state.settings
    if principal is None:
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            httponly=True,
            secure=settings.environment == "production",
            samesite="strict",
        )
        response.delete_cookie(
            CSRF_COOKIE,
            path="/",
            httponly=False,
            secure=settings.environment == "production",
            samesite="strict",
        )
        return SessionOut(
            authenticated=False,
            user_id=None,
            display_name=None,
            organization_ids=[],
            roles=[],
            capabilities=[],
            case_capabilities={},
            session_id=None,
            expires_at=None,
        )
    if principal.kind == "demonstration_viewer":
        return _session_out(session, principal)
    # Reuse the session-bound CSRF double-submit cookie; rotate only if absent or invalid.
    if principal.session_id is None:
        raise AuthenticationFailedError("Authentication is required")
    cookie_value: object = request.cookies.get(CSRF_COOKIE, "")
    raw: str = cookie_value if isinstance(cookie_value, str) else ""
    stored = session.execute(
        select(UserSession).where(UserSession.public_id == principal.session_id)
    ).scalar_one()
    if not raw or not secrets.compare_digest(
        hashlib.sha256(raw.encode()).hexdigest(), stored.csrf_hash
    ):
        raw = secrets.token_urlsafe(32)
        stored.csrf_hash = hashlib.sha256(raw.encode()).hexdigest()
        response.set_cookie(
            CSRF_COOKIE,
            raw,
            max_age=settings.session_ttl_minutes * 60,
            expires=stored.expires_at,
            httponly=False,
            secure=settings.environment == "production",
            samesite="strict",
            path="/",
        )
    session.commit()
    refreshed = Principal(
        subject=principal.subject,
        kind="user",
        authenticated=True,
        user_id=principal.user_id,
        session_id=principal.session_id,
        csrf_hash=stored.csrf_hash,
    )
    return _session_out(session, refreshed)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    session: SessionDep,
    principal: Annotated[Principal | None, Depends(get_optional_principal)],
) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    if principal and principal.kind == "user":
        check_csrf(request, principal, session)
        record = session.execute(
            select(UserSession).where(UserSession.public_id == principal.session_id)
        ).scalar_one_or_none()
        if record and record.revoked_at is None:
            record.revoked_at = utcnow()
            record_security_event(
                session,
                actor=principal.subject,
                action="session.invalidated",
                entity_type="session",
                entity_public_id=record.public_id,
                details={"reason": "logout"},
            )
            session.commit()
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=settings.environment == "production",
        samesite="strict",
    )
    response.delete_cookie(
        CSRF_COOKIE,
        path="/",
        httponly=False,
        secure=settings.environment == "production",
        samesite="strict",
    )
    return {"status": "signed_out"}
