"""Minimum administrative identity and security-audit API."""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AccessDeniedError, DomainRuleViolation, NotFoundError
from app.core.security import (
    Principal,
    organization_ids_with_capability,
    require_capability,
)
from app.db.session import get_session
from app.db.types import utcnow
from app.domain.models import (
    AuditEvent,
    Case,
    Organization,
    OrganizationMembership,
    Role,
    RoleAssignment,
    User,
    UserSession,
)
from app.domain.roles import PRIVILEGED_ROLES, ROLE_CAPABILITIES
from app.schemas import (
    ListResponse,
    RoleAssignmentIn,
    RoleOut,
    SecurityAuditOut,
    UserOut,
    UserStatusIn,
)
from app.services.records import record_security_event

router = APIRouter(prefix="/api/v1/admin", tags=["identity administration"])
SessionDep = Annotated[Session, Depends(get_session)]
UsersReader = Annotated[Principal, Depends(require_capability("users:read"))]
UsersManager = Annotated[Principal, Depends(require_capability("users:manage"))]
RolesManager = Annotated[Principal, Depends(require_capability("roles:assign"))]
SecurityAuditor = Annotated[Principal, Depends(require_capability("security_audit:read"))]
UserId = Annotated[str, Path(pattern=r"^USR-\d{3,9}$")]
RoleId = Annotated[str, Path(pattern=r"^ROLE-\d{3,9}$")]


def _admin_memberships(
    session: Session, principal: Principal, capability: str
) -> list[OrganizationMembership]:
    organization_ids = organization_ids_with_capability(session, principal, capability)
    if not organization_ids or principal.user_id is None:
        return []
    return list(
        session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == principal.user_id,
                OrganizationMembership.organization_id.in_(organization_ids),
                OrganizationMembership.status == "active",
            )
        ).scalars()
    )


def _target_user(
    session: Session,
    principal: Principal,
    user_public_id: str,
    capability: str,
) -> tuple[User, OrganizationMembership]:
    user = session.execute(
        select(User).where(User.public_id == user_public_id)
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("User was not found")
    admin_org_ids = {
        membership.organization_id
        for membership in _admin_memberships(session, principal, capability)
    }
    membership = session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.organization_id.in_(admin_org_ids or {None}),
            OrganizationMembership.status == "active",
        )
    ).scalar_one_or_none()
    if membership is None:
        raise NotFoundError("User was not found")
    return user, membership


def _user_out(session: Session, user: User, membership: OrganizationMembership) -> UserOut:
    assigned = session.execute(
        select(Role.public_id, Role.name, Case.public_id)
        .join(RoleAssignment, RoleAssignment.role_id == Role.id)
        .outerjoin(Case, Case.id == RoleAssignment.case_id)
        .where(RoleAssignment.membership_id == membership.id)
        .order_by(Role.name, Case.public_id)
    ).all()
    return UserOut(
        id=user.public_id,
        username=user.username,
        display_name=user.display_name,
        status=cast(Literal["active", "disabled"], user.status),
        organization_id=session.execute(
            select(Organization.public_id).where(Organization.id == membership.organization_id)
        ).scalar_one(),
        roles=sorted({name for _rid, name, _case in assigned}),
        case_assignments=[
            {"role_id": rid, "role": name, "case_id": case_id} for rid, name, case_id in assigned
        ],
    )


@router.get("/users", response_model=ListResponse[UserOut])
def list_users(session: SessionDep, principal: UsersReader) -> ListResponse[UserOut]:
    org_ids = {
        membership.organization_id
        for membership in _admin_memberships(session, principal, "users:read")
    }
    rows = session.execute(
        select(User, OrganizationMembership)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .where(OrganizationMembership.organization_id.in_(org_ids or {None}))
        .order_by(User.public_id)
    ).all()
    items = [_user_out(session, user, membership) for user, membership in rows]
    return ListResponse(items=items, count=len(items))


@router.get("/roles", response_model=ListResponse[RoleOut])
def list_roles(session: SessionDep, principal: RolesManager) -> ListResponse[RoleOut]:
    del principal
    roles = session.execute(select(Role).order_by(Role.name)).scalars().all()
    items = [
        RoleOut(
            id=role.public_id,
            name=role.name,
            capabilities=sorted(ROLE_CAPABILITIES.get(role.name, frozenset())),
            assignable_in_console=role.name not in PRIVILEGED_ROLES,
        )
        for role in roles
    ]
    return ListResponse(items=items, count=len(items))


@router.post("/users/{user_id}/roles", response_model=UserOut, status_code=201)
def assign_role(
    user_id: UserId, body: RoleAssignmentIn, session: SessionDep, principal: RolesManager
) -> UserOut:
    user, membership = _target_user(session, principal, user_id, "roles:assign")
    if user.id == principal.user_id:
        record_security_event(
            session,
            actor=principal.subject,
            action="authorization.denied",
            entity_type="user",
            entity_public_id=user.public_id,
            details={"reason": "self_role_assignment"},
            organization_id=membership.organization_id,
        )
        session.commit()
        raise AccessDeniedError("Administrators cannot assign roles to themselves")
    role = session.execute(select(Role).where(Role.public_id == body.role_id)).scalar_one_or_none()
    if role is None:
        raise NotFoundError("Role was not found")
    if role.name in PRIVILEGED_ROLES:
        record_security_event(
            session,
            actor=principal.subject,
            action="authorization.denied",
            entity_type="role",
            entity_public_id=role.public_id,
            details={"reason": "privileged_role_assignment"},
            organization_id=membership.organization_id,
        )
        session.commit()
        raise AccessDeniedError("Privileged roles require out-of-band operator provisioning")
    case = session.execute(select(Case).where(Case.public_id == body.case_id)).scalar_one_or_none()
    if case is None or case.organization_id != membership.organization_id:
        raise NotFoundError("Case was not found")
    existing = session.execute(
        select(RoleAssignment).where(
            RoleAssignment.membership_id == membership.id,
            RoleAssignment.role_id == role.id,
            RoleAssignment.case_id == case.id,
        )
    ).scalar_one_or_none()
    if existing:
        raise DomainRuleViolation("Role assignment already exists")
    assignment = RoleAssignment(
        membership_id=membership.id,
        role_id=role.id,
        case_id=case.id,
        created_by=principal.subject,
        updated_by=principal.subject,
    )
    session.add(assignment)
    session.flush()
    record_security_event(
        session,
        actor=principal.subject,
        action="role.assigned",
        entity_type="user",
        entity_public_id=user.public_id,
        details={"role_id": role.public_id, "case_id": case.public_id},
        organization_id=membership.organization_id,
    )
    session.commit()
    return _user_out(session, user, membership)


@router.delete("/users/{user_id}/roles/{role_id}", response_model=UserOut)
def remove_role(
    user_id: UserId,
    role_id: RoleId,
    session: SessionDep,
    principal: RolesManager,
    case_id: Annotated[str, Query(pattern=r"^CASE-\d{3,9}$")],
) -> UserOut:
    user, membership = _target_user(session, principal, user_id, "roles:assign")
    role = session.execute(select(Role).where(Role.public_id == role_id)).scalar_one_or_none()
    case = session.execute(select(Case).where(Case.public_id == case_id)).scalar_one_or_none()
    if role is None or case is None or case.organization_id != membership.organization_id:
        raise NotFoundError("Role assignment was not found")
    if role.name in PRIVILEGED_ROLES:
        record_security_event(
            session,
            actor=principal.subject,
            action="authorization.denied",
            entity_type="role",
            entity_public_id=role.public_id,
            details={"reason": "privileged_role_removal"},
            organization_id=membership.organization_id,
        )
        session.commit()
        raise AccessDeniedError("Privileged roles require out-of-band operator provisioning")
    assignment = session.execute(
        select(RoleAssignment).where(
            RoleAssignment.membership_id == membership.id,
            RoleAssignment.role_id == role.id,
            RoleAssignment.case_id == case.id,
        )
    ).scalar_one_or_none()
    if assignment is None:
        raise NotFoundError("Role assignment was not found")
    session.delete(assignment)
    record_security_event(
        session,
        actor=principal.subject,
        action="role.removed",
        entity_type="user",
        entity_public_id=user.public_id,
        details={"role_id": role.public_id, "case_id": case.public_id},
        organization_id=membership.organization_id,
    )
    session.commit()
    return _user_out(session, user, membership)


@router.patch("/users/{user_id}/status", response_model=UserOut)
def set_user_status(
    user_id: UserId, body: UserStatusIn, session: SessionDep, principal: UsersManager
) -> UserOut:
    user, membership = _target_user(session, principal, user_id, "users:manage")
    if user.id == principal.user_id:
        record_security_event(
            session,
            actor=principal.subject,
            action="authorization.denied",
            entity_type="user",
            entity_public_id=user.public_id,
            details={"reason": "self_status_change"},
            organization_id=membership.organization_id,
        )
        session.commit()
        raise DomainRuleViolation("Administrators cannot change their own status")
    if body.status == "disabled" and user.status != "disabled":
        now = utcnow()
        active_sessions = (
            session.execute(
                select(UserSession).where(
                    UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
                )
            )
            .scalars()
            .all()
        )
        for user_session in active_sessions:
            user_session.revoked_at = now
            record_security_event(
                session,
                actor=principal.subject,
                action="session.invalidated",
                entity_type="session",
                entity_public_id=user_session.public_id,
                details={"reason": "user_disabled"},
                organization_id=membership.organization_id,
            )
    user.status = body.status
    user.updated_by = principal.subject
    record_security_event(
        session,
        actor=principal.subject,
        action="user.status_changed",
        entity_type="user",
        entity_public_id=user.public_id,
        details={"status": body.status},
        organization_id=membership.organization_id,
    )
    session.commit()
    return _user_out(session, user, membership)


@router.get("/security-audit", response_model=ListResponse[SecurityAuditOut])
def security_audit(
    session: SessionDep,
    principal: SecurityAuditor,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ListResponse[SecurityAuditOut]:
    organization_ids = organization_ids_with_capability(session, principal, "security_audit:read")
    if not organization_ids:
        raise AccessDeniedError("This action is not permitted")
    events = (
        session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.organization_id.in_(organization_ids),
                AuditEvent.action.in_(
                    [
                        "authentication.success",
                        "authentication.failure",
                        "session.invalidated",
                        "authorization.denied",
                        "role.assigned",
                        "role.removed",
                        "user.status_changed",
                        "user.created",
                        "access_policy.changed",
                        "security.configuration_changed",
                    ]
                ),
            )
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.public_id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    safe_events = [
        SecurityAuditOut(
            id=event.public_id,
            occurred_at=event.occurred_at,
            actor=event.actor,
            action=event.action,
            entity_type=event.entity_type,
            entity_id=event.entity_public_id,
            details={
                key: value
                for key, value in event.details.items()
                if not any(
                    secret_word in key.casefold()
                    for secret_word in ("password", "token", "secret", "credential", "content")
                )
            },
        )
        for event in events
    ]
    return ListResponse(items=safe_events, count=len(safe_events))
