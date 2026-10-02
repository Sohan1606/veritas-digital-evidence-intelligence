"""Examination Core: exact EvidenceObject provenance and a durable execution lifecycle.

Revision ID: 0004
Revises: 0003

Appends to ``analysis_runs`` only what V2.3 execution needs. Nothing is rewritten or removed.

* ``evidence_object_id`` makes a run identify the exact EvidenceObject it examined. A composite
  foreign key ``(evidence_object_id, evidence_id, case_id)`` -> ``evidence_objects`` means the
  database itself refuses a run whose object belongs to another Evidence item or Case.
* ``idempotency_key`` + ``request_fingerprint`` and a unique ``(case_id, created_by,
  idempotency_key)`` make a repeated HTTP request unable to create a second run.
* ``cancel_requested_at``, ``last_heartbeat_at``, ``failure_code``, ``failure_message`` carry
  cancellation, liveness and sanitized failure information.
* CHECK constraints keep state and timestamps consistent; on PostgreSQL a trigger also forbids
  deleting runs, changing what a run was asked to do, changing a finished run, and any state
  change outside the lifecycle (SQLite, used only for tests, has the ORM guards instead).

V1-V2.2 never created an Analysis Run, so the table is empty in every supported deployment.
A non-empty table cannot be given exact provenance without guessing, so the upgrade stops.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATES = "state IN ('queued', 'running', 'completed', 'failed', 'cancelled')"
_LIFECYCLE = (
    "(state = 'queued' AND started_at IS NULL AND completed_at IS NULL"
    " AND last_heartbeat_at IS NULL AND cancel_requested_at IS NULL)"
    " OR (state = 'running' AND started_at IS NOT NULL AND completed_at IS NULL"
    " AND last_heartbeat_at IS NOT NULL)"
    " OR (state = 'completed' AND started_at IS NOT NULL AND completed_at IS NOT NULL"
    " AND cancel_requested_at IS NULL)"
    " OR (state = 'failed' AND started_at IS NOT NULL AND completed_at IS NOT NULL)"
    " OR (state = 'cancelled' AND completed_at IS NOT NULL)"
)
_FAILURE = (
    "(state = 'failed' AND failure_code IS NOT NULL AND failure_message IS NOT NULL)"
    " OR (state != 'failed' AND failure_code IS NULL AND failure_message IS NULL)"
)

_GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION veritas_analysis_runs_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'analysis runs are execution history and cannot be deleted'
            USING ERRCODE = '55000';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.public_id IS DISTINCT FROM OLD.public_id
       OR NEW.case_id IS DISTINCT FROM OLD.case_id
       OR NEW.evidence_id IS DISTINCT FROM OLD.evidence_id
       OR NEW.evidence_object_id IS DISTINCT FROM OLD.evidence_object_id
       OR NEW.method_key IS DISTINCT FROM OLD.method_key
       OR NEW.method_version IS DISTINCT FROM OLD.method_version
       OR NEW.parameters IS DISTINCT FROM OLD.parameters
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.request_fingerprint IS DISTINCT FROM OLD.request_fingerprint
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
        RAISE EXCEPTION 'analysis run identity and execution request are immutable'
            USING ERRCODE = '55000';
    END IF;
    IF OLD.state IN ('completed', 'failed', 'cancelled') THEN
        RAISE EXCEPTION 'finished analysis runs are immutable' USING ERRCODE = '55000';
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
        (OLD.state = 'queued' AND NEW.state IN ('running', 'cancelled'))
        OR (OLD.state = 'running' AND NEW.state IN ('completed', 'failed', 'cancelled', 'queued'))
    ) THEN
        RAISE EXCEPTION 'invalid analysis run lifecycle transition' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    legacy = op.get_bind().execute(sa.text("SELECT count(*) FROM analysis_runs")).scalar_one()
    if legacy:
        raise RuntimeError(
            f"analysis_runs holds {legacy} row(s) created before V2.3. They have no recorded "
            "EvidenceObject, and exact provenance is never guessed. Resolve them deliberately "
            "(they cannot have been produced by any V1-V2.2 code path) before upgrading."
        )

    op.create_index(
        "uq_evidence_objects_provenance",
        "evidence_objects",
        ["id", "evidence_id", "case_id"],
        unique=True,
    )

    with op.batch_alter_table("analysis_runs") as batch:
        batch.add_column(sa.Column("evidence_object_id", sa.Uuid(), nullable=False))
        batch.add_column(sa.Column("idempotency_key", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("request_fingerprint", sa.String(length=64), nullable=False))
        batch.add_column(sa.Column("cancel_requested_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("failure_code", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("failure_message", sa.String(length=500), nullable=True))
        batch.create_foreign_key(
            "fk_analysis_runs_evidence_object_id_evidence_objects",
            "evidence_objects",
            ["evidence_object_id", "evidence_id", "case_id"],
            ["id", "evidence_id", "case_id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(op.f("ck_analysis_runs_run_state"), _STATES)
        batch.create_check_constraint(op.f("ck_analysis_runs_lifecycle_consistency"), _LIFECYCLE)
        batch.create_check_constraint(op.f("ck_analysis_runs_failure_consistency"), _FAILURE)
        batch.create_check_constraint(
            op.f("ck_analysis_runs_fingerprint_length"), "length(request_fingerprint) = 64"
        )
        batch.create_unique_constraint(
            "uq_analysis_runs_idempotency", ["case_id", "created_by", "idempotency_key"]
        )
        batch.create_index("ix_analysis_runs_evidence_object_id", ["evidence_object_id"])
        batch.create_index("ix_analysis_runs_state_created_at", ["state", "created_at"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute(_GUARD_FUNCTION)
        op.execute(
            "CREATE TRIGGER analysis_runs_guard BEFORE UPDATE OR DELETE ON analysis_runs "
            "FOR EACH ROW EXECUTE FUNCTION veritas_analysis_runs_guard()"
        )


def downgrade() -> None:
    """Destructive for V2.3 execution provenance: the columns that identify the examined
    EvidenceObject are dropped. Take a backup first; the repository only downgrades
    disposable databases."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS analysis_runs_guard ON analysis_runs")
        op.execute("DROP FUNCTION IF EXISTS veritas_analysis_runs_guard()")

    with op.batch_alter_table("analysis_runs") as batch:
        batch.drop_index("ix_analysis_runs_state_created_at")
        batch.drop_index("ix_analysis_runs_evidence_object_id")
        batch.drop_constraint("uq_analysis_runs_idempotency", type_="unique")
        batch.drop_constraint(op.f("ck_analysis_runs_fingerprint_length"), type_="check")
        batch.drop_constraint(op.f("ck_analysis_runs_failure_consistency"), type_="check")
        batch.drop_constraint(op.f("ck_analysis_runs_lifecycle_consistency"), type_="check")
        batch.drop_constraint(op.f("ck_analysis_runs_run_state"), type_="check")
        batch.drop_constraint(
            "fk_analysis_runs_evidence_object_id_evidence_objects", type_="foreignkey"
        )
        for column in (
            "failure_message",
            "failure_code",
            "last_heartbeat_at",
            "cancel_requested_at",
            "request_fingerprint",
            "idempotency_key",
            "evidence_object_id",
        ):
            batch.drop_column(column)

    op.drop_index("uq_evidence_objects_provenance", table_name="evidence_objects")
