"""Out-of-band account and privileged-role provisioning; prompts never echo passwords."""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.core.authentication import hash_provisioned_password
from app.core.config import get_settings
from app.db.session import build_engine, build_session_factory
from app.domain.models import (
    Case,
    Organization,
    OrganizationMembership,
    Role,
    RoleAssignment,
    User,
)
from app.domain.roles import PRIVILEGED_ROLES
from app.services.records import record_security_event


def _password() -> str:
    first = getpass.getpass("New password (minimum 12 characters): ")
    second = getpass.getpass("Confirm password: ")
    if first != second or len(first) < 12 or len(first) > 1024:
        raise ValueError("Passwords must match and be between 12 and 1024 characters")
    return first


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision a VERITAS identity (no public signup)")
    parser.add_argument("--username", required=True, help="Unique sign-in name")
    parser.add_argument("--display-name", required=True)
    parser.add_argument(
        "--role",
        required=True,
        choices=["INVESTIGATOR", "REVIEWER", "CUSTODIAN", "ADMINISTRATOR", "AUDITOR", "RESEARCHER"],
    )
    parser.add_argument("--case-id", help="Required for non-privileged case-scoped roles")
    args = parser.parse_args()
    username = args.username.strip().casefold()
    if (
        not username
        or len(username) > 128
        or not args.display_name.strip()
        or len(args.display_name) > 160
    ):
        parser.error("username or display name has invalid length")
    if args.role not in PRIVILEGED_ROLES and not args.case_id:
        parser.error("--case-id is required for case-scoped roles")
    if args.role in PRIVILEGED_ROLES and args.case_id:
        parser.error("privileged roles are organization-scoped; omit --case-id")
    try:
        password = _password()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    settings = get_settings()
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    try:
        with factory() as session, session.begin():
            if session.execute(
                select(User.id).where(User.username == username)
            ).scalar_one_or_none():
                print("That username is already provisioned.", file=sys.stderr)
                return 2
            organization = (
                session.execute(
                    select(Organization)
                    .where(Organization.status == "active")
                    .order_by(Organization.public_id)
                )
                .scalars()
                .first()
            )
            role = session.execute(select(Role).where(Role.name == args.role)).scalar_one_or_none()
            if organization is None or role is None:
                print(
                    "Identity schema is not provisioned; apply migrations first.", file=sys.stderr
                )
                return 2
            case = None
            if args.case_id:
                case = session.execute(
                    select(Case).where(Case.public_id == args.case_id)
                ).scalar_one_or_none()
                if case is None or case.organization_id != organization.id:
                    print("Case was not found in the provisioned Organization.", file=sys.stderr)
                    return 2
            user = User(
                username=username,
                display_name=args.display_name.strip(),
                status="active",
                password_hash=hash_provisioned_password(password),
                auth_provider=None,
                auth_subject=None,
                created_by="system:provisioning",
                updated_by="system:provisioning",
            )
            session.add(user)
            session.flush()
            membership = OrganizationMembership(
                user_id=user.id,
                organization_id=organization.id,
                status="active",
                created_by="system:provisioning",
                updated_by="system:provisioning",
            )
            session.add(membership)
            session.flush()
            assignment = RoleAssignment(
                membership_id=membership.id,
                role_id=role.id,
                case_id=case.id if case else None,
                created_by="system:provisioning",
                updated_by="system:provisioning",
            )
            session.add(assignment)
            session.flush()
            record_security_event(
                session,
                actor="system:provisioning",
                action="user.created",
                entity_type="user",
                entity_public_id=user.public_id,
                details={"role_id": role.public_id, "case_id": case.public_id if case else None},
                organization_id=organization.id,
            )
            print(f"Provisioned {user.public_id} with role {role.name}.")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
