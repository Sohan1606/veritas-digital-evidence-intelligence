"""Evidence Intake & Integrity Foundation.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Uuid:
    return sa.Uuid(as_uuid=True)


_CUSTODY_GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION veritas_evidence_custody_events_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'evidence custody events are append-only' USING ERRCODE = '55000';
END;
$$;
"""


def upgrade() -> None:
    op.create_table(
        "evidence_objects",
        sa.Column("case_id", _id(), nullable=False),
        sa.Column("organization_id", _id(), nullable=False),
        sa.Column("evidence_id", _id(), nullable=False),
        sa.Column("storage_key", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("declared_media_type", sa.String(length=127), nullable=False),
        sa.Column("detected_media_type", sa.String(length=127), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("sha512", sa.String(length=128), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("validation_note", sa.String(length=500), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acquired_by", sa.String(length=128), nullable=False),
        sa.Column("upload_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preserved_by", sa.String(length=128), nullable=True),
        sa.Column("id", _id(), nullable=False),
        sa.Column("public_id", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "state IN ('QUARANTINED', 'PRESERVED', 'REJECTED')",
            name=op.f("ck_evidence_objects_object_state"),
        ),
        sa.CheckConstraint(
            "validation_status IN ('pending', 'accepted', 'rejected')",
            name=op.f("ck_evidence_objects_validation_status"),
        ),
        sa.CheckConstraint(
            "byte_size >= 0", name=op.f("ck_evidence_objects_byte_size_nonnegative")
        ),
        sa.CheckConstraint(
            "sha256 IS NULL OR length(sha256) = 64",
            name=op.f("ck_evidence_objects_sha256_length"),
        ),
        sa.CheckConstraint(
            "sha512 IS NULL OR length(sha512) = 128",
            name=op.f("ck_evidence_objects_sha512_length"),
        ),
        sa.CheckConstraint(
            "(upload_completed_at IS NULL AND sha256 IS NULL AND sha512 IS NULL AND byte_size = 0)"
            " OR (upload_completed_at IS NOT NULL AND sha256 IS NOT NULL AND sha512 IS NOT NULL)",
            name=op.f("ck_evidence_objects_upload_digest_consistency"),
        ),
        sa.CheckConstraint(
            "state != 'PRESERVED' OR (upload_completed_at IS NOT NULL AND detected_media_type IS NOT NULL"
            " AND validation_status = 'accepted' AND preserved_at IS NOT NULL AND preserved_by IS NOT NULL)",
            name=op.f("ck_evidence_objects_preserved_metadata_consistency"),
        ),
        sa.CheckConstraint(
            "state != 'REJECTED' OR validation_status = 'rejected'",
            name=op.f("ck_evidence_objects_rejected_validation_consistency"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_evidence_objects_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_evidence_objects_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.id"],
            name=op.f("fk_evidence_objects_evidence_id_evidence"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_objects")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_evidence_objects_storage_key")),
    )
    op.create_index(
        op.f("ix_evidence_objects_case_id"), "evidence_objects", ["case_id"], unique=False
    )
    op.create_index(
        op.f("ix_evidence_objects_organization_id"),
        "evidence_objects",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evidence_objects_evidence_id"), "evidence_objects", ["evidence_id"], unique=False
    )
    op.create_index(
        op.f("ix_evidence_objects_public_id"), "evidence_objects", ["public_id"], unique=True
    )

    op.create_table(
        "evidence_custody_events",
        sa.Column("case_id", _id(), nullable=False),
        sa.Column("organization_id", _id(), nullable=False),
        sa.Column("evidence_id", _id(), nullable=False),
        sa.Column("evidence_object_id", _id(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("from_state", sa.String(length=32), nullable=True),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", _id(), nullable=False),
        sa.Column("counterparty_user_id", _id(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("id", _id(), nullable=False),
        sa.Column("public_id", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('RECEIVED', 'PRESERVED')",
            name=op.f("ck_evidence_custody_events_custody_event_type"),
        ),
        sa.CheckConstraint(
            "(event_type = 'RECEIVED' AND from_state IS NULL AND to_state = 'QUARANTINED')"
            " OR (event_type = 'PRESERVED' AND from_state = 'QUARANTINED'"
            " AND to_state = 'PRESERVED')",
            name=op.f("ck_evidence_custody_events_custody_state_transition"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_evidence_custody_events_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_evidence_custody_events_organization_id_organizations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.id"],
            name=op.f("fk_evidence_custody_events_evidence_id_evidence"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_object_id"],
            ["evidence_objects.id"],
            name=op.f("fk_evidence_custody_events_evidence_object_id_evidence_objects"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_evidence_custody_events_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["counterparty_user_id"],
            ["users.id"],
            name=op.f("fk_evidence_custody_events_counterparty_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_custody_events")),
    )
    for column in (
        "case_id",
        "organization_id",
        "evidence_id",
        "evidence_object_id",
        "actor_user_id",
    ):
        op.create_index(
            op.f(f"ix_evidence_custody_events_{column}"),
            "evidence_custody_events",
            [column],
            unique=False,
        )
    op.create_index(
        op.f("ix_evidence_custody_events_public_id"),
        "evidence_custody_events",
        ["public_id"],
        unique=True,
    )

    op.execute(
        sa.text(
            "INSERT INTO identifier_sequences (prefix, last_value) VALUES (:prefix, 0)"
        ).bindparams(prefix="EOBJ")
    )
    op.execute(
        sa.text(
            "INSERT INTO identifier_sequences (prefix, last_value) VALUES (:prefix, 0)"
        ).bindparams(prefix="CST")
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(_CUSTODY_GUARD_FUNCTION)
        op.execute(
            "CREATE TRIGGER evidence_custody_events_append_only "
            "BEFORE UPDATE OR DELETE ON evidence_custody_events "
            "FOR EACH ROW EXECUTE FUNCTION veritas_evidence_custody_events_append_only()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS evidence_custody_events_append_only ON evidence_custody_events"
        )
        op.execute("DROP FUNCTION IF EXISTS veritas_evidence_custody_events_append_only()")
    op.execute(sa.text("DELETE FROM identifier_sequences WHERE prefix IN ('EOBJ', 'CST')"))
    op.drop_index(
        op.f("ix_evidence_custody_events_public_id"), table_name="evidence_custody_events"
    )
    for column in (
        "actor_user_id",
        "evidence_object_id",
        "evidence_id",
        "organization_id",
        "case_id",
    ):
        op.drop_index(
            op.f(f"ix_evidence_custody_events_{column}"), table_name="evidence_custody_events"
        )
    op.drop_table("evidence_custody_events")
    op.drop_index(op.f("ix_evidence_objects_public_id"), table_name="evidence_objects")
    for column in ("evidence_id", "organization_id", "case_id"):
        op.drop_index(op.f(f"ix_evidence_objects_{column}"), table_name="evidence_objects")
    op.drop_table("evidence_objects")
