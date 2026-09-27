"""V1 foundation: canonical entities, identifier sequences, append-only audit.

Revision ID: 0001
Revises:
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDIT_GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION veritas_audit_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("is_demonstration", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cases")),
    )
    op.create_index(op.f("ix_cases_public_id"), "cases", ["public_id"], unique=True)
    op.create_table(
        "identifier_sequences",
        sa.Column("prefix", sa.String(length=8), nullable=False),
        sa.Column("last_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("prefix", name=op.f("pk_identifier_sequences")),
    )
    op.create_table(
        "audit_events",
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_public_id", sa.String(length=24), nullable=True),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "details", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(length=24), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_audit_events_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(op.f("ix_audit_events_case_id"), "audit_events", ["case_id"], unique=False)
    op.create_index(
        op.f("ix_audit_events_occurred_at"), "audit_events", ["occurred_at"], unique=False
    )
    op.create_index(op.f("ix_audit_events_public_id"), "audit_events", ["public_id"], unique=True)
    op.create_table(
        "case_relationships",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("relationship_type", sa.String(length=32), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_case_relationships_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_relationships")),
        sa.UniqueConstraint(
            "case_id",
            "source_type",
            "source_id",
            "relationship_type",
            "target_type",
            "target_id",
            name="uq_case_relationships_edge",
        ),
    )
    op.create_index(
        op.f("ix_case_relationships_case_id"), "case_relationships", ["case_id"], unique=False
    )
    op.create_index(
        op.f("ix_case_relationships_public_id"), "case_relationships", ["public_id"], unique=True
    )
    op.create_table(
        "claims",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_claims_case_id_cases"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claims")),
    )
    op.create_index(op.f("ix_claims_case_id"), "claims", ["case_id"], unique=False)
    op.create_index(op.f("ix_claims_public_id"), "claims", ["public_id"], unique=True)
    op.create_table(
        "evidence",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("evidence_type", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_evidence_case_id_cases"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence")),
    )
    op.create_index(op.f("ix_evidence_case_id"), "evidence", ["case_id"], unique=False)
    op.create_index(op.f("ix_evidence_public_id"), "evidence", ["public_id"], unique=True)
    op.create_table(
        "findings",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column(
            "limitations", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column(
            "alternative_explanations",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_findings_case_id_cases"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_findings")),
    )
    op.create_index(op.f("ix_findings_case_id"), "findings", ["case_id"], unique=False)
    op.create_index(op.f("ix_findings_public_id"), "findings", ["public_id"], unique=True)
    op.create_table(
        "objectives",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_objectives_case_id_cases"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_objectives")),
    )
    op.create_index(op.f("ix_objectives_case_id"), "objectives", ["case_id"], unique=False)
    op.create_index(op.f("ix_objectives_public_id"), "objectives", ["public_id"], unique=True)
    op.create_table(
        "analysis_runs",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("method_key", sa.String(length=64), nullable=False),
        sa.Column("method_version", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column(
            "parameters", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_analysis_runs_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.id"],
            name=op.f("fk_analysis_runs_evidence_id_evidence"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_runs")),
    )
    op.create_index(op.f("ix_analysis_runs_case_id"), "analysis_runs", ["case_id"], unique=False)
    op.create_index(
        op.f("ix_analysis_runs_evidence_id"), "analysis_runs", ["evidence_id"], unique=False
    )
    op.create_index(op.f("ix_analysis_runs_public_id"), "analysis_runs", ["public_id"], unique=True)
    op.create_table(
        "assessments",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("assessed_by", sa.String(length=128), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_assessments_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name=op.f("fk_assessments_claim_id_claims"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessments")),
    )
    op.create_index(op.f("ix_assessments_case_id"), "assessments", ["case_id"], unique=False)
    op.create_index(op.f("ix_assessments_claim_id"), "assessments", ["claim_id"], unique=False)
    op.create_index(op.f("ix_assessments_public_id"), "assessments", ["public_id"], unique=True)
    op.create_table(
        "evidence_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column(
            "identity", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column(
            "integrity", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column(
            "provenance", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column(
            "quality", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column(
            "acquisition_context",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "classification",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.id"],
            name=op.f("fk_evidence_profiles_evidence_id_evidence"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_profiles")),
        sa.UniqueConstraint("evidence_id", name=op.f("uq_evidence_profiles_evidence_id")),
    )
    op.create_table(
        "observations",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_run_id", sa.Uuid(), nullable=True),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "(origin = 'analysis_run' AND analysis_run_id IS NOT NULL) OR (origin = 'manual' AND analysis_run_id IS NULL)",
            name=op.f("ck_observations_origin_consistency"),
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["analysis_runs.id"],
            name=op.f("fk_observations_analysis_run_id_analysis_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
            name=op.f("fk_observations_case_id_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.id"],
            name=op.f("fk_observations_evidence_id_evidence"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_observations")),
    )
    op.create_index(
        op.f("ix_observations_analysis_run_id"), "observations", ["analysis_run_id"], unique=False
    )
    op.create_index(op.f("ix_observations_case_id"), "observations", ["case_id"], unique=False)
    op.create_index(
        op.f("ix_observations_evidence_id"), "observations", ["evidence_id"], unique=False
    )
    op.create_index(op.f("ix_observations_public_id"), "observations", ["public_id"], unique=True)

    # Audit Events are append-only. The ORM already refuses updates/deletes; on PostgreSQL
    # the database enforces it too, so no client (including ad-hoc SQL) can rewrite history.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(AUDIT_GUARD_FUNCTION)
        op.execute(
            "CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events "
            "FOR EACH ROW EXECUTE FUNCTION veritas_audit_events_append_only()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events")
        op.execute("DROP FUNCTION IF EXISTS veritas_audit_events_append_only()")
    op.drop_index(op.f("ix_observations_public_id"), table_name="observations")
    op.drop_index(op.f("ix_observations_evidence_id"), table_name="observations")
    op.drop_index(op.f("ix_observations_case_id"), table_name="observations")
    op.drop_index(op.f("ix_observations_analysis_run_id"), table_name="observations")
    op.drop_table("observations")
    op.drop_table("evidence_profiles")
    op.drop_index(op.f("ix_assessments_public_id"), table_name="assessments")
    op.drop_index(op.f("ix_assessments_claim_id"), table_name="assessments")
    op.drop_index(op.f("ix_assessments_case_id"), table_name="assessments")
    op.drop_table("assessments")
    op.drop_index(op.f("ix_analysis_runs_public_id"), table_name="analysis_runs")
    op.drop_index(op.f("ix_analysis_runs_evidence_id"), table_name="analysis_runs")
    op.drop_index(op.f("ix_analysis_runs_case_id"), table_name="analysis_runs")
    op.drop_table("analysis_runs")
    op.drop_index(op.f("ix_objectives_public_id"), table_name="objectives")
    op.drop_index(op.f("ix_objectives_case_id"), table_name="objectives")
    op.drop_table("objectives")
    op.drop_index(op.f("ix_findings_public_id"), table_name="findings")
    op.drop_index(op.f("ix_findings_case_id"), table_name="findings")
    op.drop_table("findings")
    op.drop_index(op.f("ix_evidence_public_id"), table_name="evidence")
    op.drop_index(op.f("ix_evidence_case_id"), table_name="evidence")
    op.drop_table("evidence")
    op.drop_index(op.f("ix_claims_public_id"), table_name="claims")
    op.drop_index(op.f("ix_claims_case_id"), table_name="claims")
    op.drop_table("claims")
    op.drop_index(op.f("ix_case_relationships_public_id"), table_name="case_relationships")
    op.drop_index(op.f("ix_case_relationships_case_id"), table_name="case_relationships")
    op.drop_table("case_relationships")
    op.drop_index(op.f("ix_audit_events_public_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_occurred_at"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_case_id"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("identifier_sequences")
    op.drop_index(op.f("ix_cases_public_id"), table_name="cases")
    op.drop_table("cases")
