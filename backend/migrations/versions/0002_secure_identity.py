"""Secure identities, role assignments, server-side sessions and organization scope.

Revision ID: 0002
Revises: 0001
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ROLE_ROWS = (
    ("ROLE-001", "INVESTIGATOR"),
    ("ROLE-002", "REVIEWER"),
    ("ROLE-003", "CUSTODIAN"),
    ("ROLE-004", "ADMINISTRATOR"),
    ("ROLE-005", "AUDITOR"),
    ("ROLE-006", "RESEARCHER"),
)


def _id() -> sa.Uuid:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("id", _id(), nullable=False),
        sa.Column("public_id", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        sa.UniqueConstraint("name", name=op.f("uq_organizations_name")),
    )
    op.create_index(op.f("ix_organizations_public_id"), "organizations", ["public_id"], unique=True)
    op.create_table(
        "roles",
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("id", _id(), nullable=False),
        sa.Column("public_id", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("name", name=op.f("uq_roles_name")),
    )
    op.create_index(op.f("ix_roles_public_id"), "roles", ["public_id"], unique=True)
    op.create_table(
        "users",
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("auth_provider", sa.String(64), nullable=True),
        sa.Column("auth_subject", sa.String(255), nullable=True),
        sa.Column("id", _id(), nullable=False),
        sa.Column("public_id", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_users_user_status"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("auth_provider", "auth_subject", name="uq_user_auth_linkage"),
    )
    op.create_index(op.f("ix_users_public_id"), "users", ["public_id"], unique=True)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_table(
        "organization_memberships",
        sa.Column("user_id", _id(), nullable=False),
        sa.Column("organization_id", _id(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("id", _id(), nullable=False),
        sa.Column("public_id", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_organization_memberships_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_organization_memberships_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organization_memberships")),
        sa.UniqueConstraint("user_id", "organization_id", name="uq_membership_user_org"),
    )
    op.create_index(
        op.f("ix_organization_memberships_organization_id"),
        "organization_memberships",
        ["organization_id"],
    )
    op.create_index(
        op.f("ix_organization_memberships_public_id"),
        "organization_memberships",
        ["public_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_organization_memberships_user_id"), "organization_memberships", ["user_id"]
    )

    # Organization is provisioned once. Existing records are mapped to it, not re-created.
    op.bulk_insert(
        sa.table(
            "organizations",
            sa.column("name", sa.String),
            sa.column("status", sa.String),
            sa.column("id", _id()),
            sa.column("public_id", sa.String),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
            sa.column("created_by", sa.String),
            sa.column("updated_by", sa.String),
        ),
        [
            {
                "name": "VERITAS Organization",
                "status": "active",
                "id": ORG_UUID,
                "public_id": "ORG-001",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "created_by": "system:migration",
                "updated_by": "system:migration",
            }
        ],
    )
    op.add_column("cases", sa.Column("organization_id", _id(), nullable=True))
    op.execute(sa.text("UPDATE cases SET organization_id = :org").bindparams(org=ORG_UUID))
    with op.batch_alter_table("cases") as batch:
        batch.alter_column("organization_id", existing_type=_id(), nullable=False)
        batch.create_foreign_key(
            op.f("fk_cases_organization_id_organizations"),
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(op.f("ix_cases_organization_id"), ["organization_id"])

    # Security events share the canonical AuditEvent table. The nullable organization
    # association scopes org-owned events; genuinely global events stay unscoped.
    with op.batch_alter_table("audit_events") as batch:
        batch.add_column(sa.Column("organization_id", _id(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_audit_events_organization_id_organizations"),
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(op.f("ix_audit_events_organization_id"), ["organization_id"])
    op.execute(
        sa.text(
            "UPDATE audit_events SET organization_id = "
            "(SELECT organization_id FROM cases WHERE cases.id = audit_events.case_id) "
            "WHERE case_id IS NOT NULL"
        )
    )

    op.create_table(
        "user_sessions",
        sa.Column("user_id", _id(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("id", _id(), nullable=False),
        sa.Column("public_id", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_sessions_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sessions")),
    )
    op.create_index(op.f("ix_user_sessions_expires_at"), "user_sessions", ["expires_at"])
    op.create_index(op.f("ix_user_sessions_public_id"), "user_sessions", ["public_id"], unique=True)
    op.create_index(
        op.f("ix_user_sessions_token_hash"), "user_sessions", ["token_hash"], unique=True
    )
    op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"])
    op.create_table(
        "login_attempts",
        sa.Column("bucket", sa.String(64), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("bucket", name=op.f("pk_login_attempts")),
    )
    op.create_table(
        "role_assignments",
        sa.Column("membership_id", _id(), nullable=False),
        sa.Column("role_id", _id(), nullable=False),
        sa.Column("case_id", _id(), nullable=True),
        sa.Column("id", _id(), nullable=False),
        sa.Column("public_id", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_role_assignments_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["organization_memberships.id"],
            name=op.f("fk_role_assignments_membership_id_organization_memberships"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name=op.f("fk_role_assignments_role_id_roles"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_assignments")),
        sa.UniqueConstraint("membership_id", "role_id", "case_id", name="uq_role_assignment_scope"),
    )
    for column in ("case_id", "membership_id", "role_id"):
        op.create_index(op.f(f"ix_role_assignments_{column}"), "role_assignments", [column])
    op.create_index(
        op.f("ix_role_assignments_public_id"), "role_assignments", ["public_id"], unique=True
    )

    op.bulk_insert(
        sa.table(
            "roles",
            sa.column("name", sa.String),
            sa.column("id", _id()),
            sa.column("public_id", sa.String),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
            sa.column("created_by", sa.String),
            sa.column("updated_by", sa.String),
        ),
        [
            {
                "name": name,
                "id": uuid.uuid5(uuid.NAMESPACE_URL, f"veritas:{name}"),
                "public_id": public_id,
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "created_by": "system:migration",
                "updated_by": "system:migration",
            }
            for public_id, name in ROLE_ROWS
        ],
    )
    for prefix, last in (("ORG", 1), ("ROLE", len(ROLE_ROWS))):
        op.execute(
            sa.text(
                "INSERT INTO identifier_sequences (prefix, last_value) VALUES (:prefix, :last)"
            ).bindparams(prefix=prefix, last=last)
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_role_assignments_public_id"), table_name="role_assignments")
    for column in ("case_id", "membership_id", "role_id"):
        op.drop_index(op.f(f"ix_role_assignments_{column}"), table_name="role_assignments")
    op.drop_table("role_assignments")
    op.drop_table("login_attempts")
    for index in (
        "ix_user_sessions_user_id",
        "ix_user_sessions_token_hash",
        "ix_user_sessions_public_id",
        "ix_user_sessions_expires_at",
    ):
        op.drop_index(op.f(index), table_name="user_sessions")
    op.drop_table("user_sessions")
    with op.batch_alter_table("cases") as batch:
        batch.drop_index(op.f("ix_cases_organization_id"))
        batch.drop_constraint(op.f("fk_cases_organization_id_organizations"), type_="foreignkey")
        batch.drop_column("organization_id")
    for table, indexes in (
        (
            "organization_memberships",
            (
                "ix_organization_memberships_user_id",
                "ix_organization_memberships_public_id",
                "ix_organization_memberships_organization_id",
            ),
        ),
        ("users", ("ix_users_username", "ix_users_public_id")),
        ("roles", ("ix_roles_public_id",)),
        ("organizations", ("ix_organizations_public_id",)),
    ):
        for index in indexes:
            op.drop_index(op.f(index), table_name=table)
    op.drop_table("organization_memberships")
    op.drop_table("users")
    op.drop_table("roles")
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_index(op.f("ix_audit_events_organization_id"))
        batch.drop_constraint(
            op.f("fk_audit_events_organization_id_organizations"), type_="foreignkey"
        )
        batch.drop_column("organization_id")
    op.drop_table("organizations")
    op.execute(sa.text("DELETE FROM identifier_sequences WHERE prefix IN ('ORG', 'ROLE')"))
