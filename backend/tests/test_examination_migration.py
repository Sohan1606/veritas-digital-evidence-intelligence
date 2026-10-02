"""Migration 0004 (Examination Core): append-only chain, refusal to guess, up/down/up, PG."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.db.migrations import alembic_config
from app.seed import seed_demonstration_data

NEW_COLUMNS = {
    "evidence_object_id", "idempotency_key", "request_fingerprint", "cancel_requested_at",
    "last_heartbeat_at", "failure_code", "failure_message",
}  # fmt: skip
CONSTRAINTS = {
    "ck_analysis_runs_run_state",
    "ck_analysis_runs_lifecycle_consistency",
    "ck_analysis_runs_failure_consistency",
    "ck_analysis_runs_fingerprint_length",
}

# SHA-256 of each earlier migration with line endings normalized (so a Windows checkout with
# CRLF hashes the same). The chain is append-only: these files must never be edited.
EARLIER_MIGRATIONS = {
    "0001_foundation.py": "25aa98136677d915ebdf05a4a7e8802faa510b621144089f261e3705cad2aea3",
    "0002_secure_identity.py": "35cd9902a60ebf887fed83dc7d219ad336f951cace5ace396c52c8a2f3677bc2",
    "0003_evidence_intake_integrity.py": (
        "ff5591c6b51fba0eff4dd89ecf880bd9e60230d24357c76dfe1d0e71733388e3"
    ),
}
VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _config(url: str) -> Config:
    config = alembic_config(url)
    config.attributes["configure_logger"] = False
    return config


def _sqlite_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'migration.sqlite3'}"


# --- The chain ------------------------------------------------------------------------------


def test_the_revision_chain_is_linear_append_only_and_ends_at_0004() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_heads() == ["0004"]
    chain = [r.revision for r in script.walk_revisions()]
    assert chain == ["0004", "0003", "0002", "0001"]
    head = script.get_revision("0004")
    assert head is not None and head.down_revision == "0003"


@pytest.mark.parametrize("name", sorted(EARLIER_MIGRATIONS))
def test_earlier_migrations_are_unchanged(name: str) -> None:
    normalized = "\n".join((VERSIONS / name).read_text(encoding="utf-8").splitlines())
    assert hashlib.sha256(normalized.encode()).hexdigest() == EARLIER_MIGRATIONS[name], (
        f"{name} was edited; migrations are append-only"
    )


# --- SQLite (tests only) --------------------------------------------------------------------


def test_upgrade_downgrade_upgrade_adds_and_removes_exactly_the_v23_schema(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)
    config = _config(url)
    command.upgrade(config, "0003")
    engine = create_engine(url)
    try:
        before = {c["name"] for c in inspect(engine).get_columns("analysis_runs")}
        assert not NEW_COLUMNS & before
        old_indexes = {i["name"] for i in inspect(engine).get_indexes("evidence_objects")}
        assert "uq_evidence_objects_provenance" not in old_indexes

        command.upgrade(config, "head")
        inspector = inspect(engine)
        columns = {c["name"]: c for c in inspector.get_columns("analysis_runs")}
        assert set(columns) >= NEW_COLUMNS
        assert columns["evidence_object_id"]["nullable"] is False
        assert columns["request_fingerprint"]["nullable"] is False
        assert columns["idempotency_key"]["nullable"] is True
        assert {c["name"] for c in inspector.get_check_constraints("analysis_runs")} >= CONSTRAINTS
        (fk,) = [
            f
            for f in inspector.get_foreign_keys("analysis_runs")
            if f["referred_table"] == "evidence_objects"
        ]
        assert fk["constrained_columns"] == ["evidence_object_id", "evidence_id", "case_id"]
        assert fk["referred_columns"] == ["id", "evidence_id", "case_id"]
        assert fk["options"].get("ondelete") == "RESTRICT"
        assert any(
            u["name"] == "uq_analysis_runs_idempotency"
            and u["column_names"] == ["case_id", "created_by", "idempotency_key"]
            for u in inspector.get_unique_constraints("analysis_runs")
        )
        indexes = {i["name"]: i for i in inspector.get_indexes("analysis_runs")}
        assert indexes["ix_analysis_runs_state_created_at"]["column_names"] == [
            "state",
            "created_at",
        ]
        assert "ix_analysis_runs_evidence_object_id" in indexes
        provenance = {i["name"]: i for i in inspector.get_indexes("evidence_objects")}
        assert provenance["uq_evidence_objects_provenance"]["unique"]

        command.downgrade(config, "0003")
        inspector = inspect(engine)
        assert not NEW_COLUMNS & {c["name"] for c in inspector.get_columns("analysis_runs")}
        assert "uq_evidence_objects_provenance" not in {
            i["name"] for i in inspector.get_indexes("evidence_objects")
        }
        # V1 columns survived the round trip untouched.
        assert {"case_id", "evidence_id", "method_key", "state", "parameters"} <= {
            c["name"] for c in inspector.get_columns("analysis_runs")
        }

        command.upgrade(config, "head")
        assert {c["name"] for c in inspect(engine).get_columns("analysis_runs")} >= NEW_COLUMNS
    finally:
        engine.dispose()


def test_upgrade_refuses_to_guess_provenance_for_runs_that_predate_v23(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)
    config = _config(url)
    command.upgrade(config, "0003")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO analysis_runs (case_id, evidence_id, method_key, method_version,"
                    " state, parameters, id, public_id, created_at, updated_at, created_by,"
                    " updated_by) VALUES ('c', 'e', 'legacy.method', '1.0', 'queued', '{}',"
                    " 'r', 'ANL-001', '2026-01-01', '2026-01-01', 'test', 'test')"
                )
            )
        with pytest.raises(RuntimeError, match="exact provenance is never guessed"):
            command.upgrade(config, "head")
        # The refusal is clean: still at 0003, schema untouched, the legacy row intact.
        inspector = inspect(engine)
        assert not NEW_COLUMNS & {c["name"] for c in inspector.get_columns("analysis_runs")}
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0003"
            )
            assert connection.execute(text("SELECT count(*) FROM analysis_runs")).scalar_one() == 1
    finally:
        engine.dispose()


# --- PostgreSQL -----------------------------------------------------------------------------


def _postgres_admin_url() -> str:
    url = os.environ.get("VERITAS_TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("PostgreSQL migration checks need VERITAS_TEST_DATABASE_URL (SQLite run)")
    return url


@contextmanager
def scratch_postgres_database() -> Iterator[str]:
    """A disposable database on the test server; never any shared or protected database."""
    admin_url = make_url(_postgres_admin_url())
    name = f"veritas_mig_{uuid4().hex[:12]}"
    admin = create_engine(admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            try:
                connection.execute(text(f'CREATE DATABASE "{name}"'))
            except Exception:
                pytest.skip("the test role may not create a scratch database")
        try:
            yield admin_url.set(database=name).render_as_string(hide_password=False)
        finally:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()


def test_postgres_upgrade_installs_and_downgrade_removes_the_guard_trigger() -> None:
    with scratch_postgres_database() as url:
        config = _config(url)
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                trigger_query = text(
                    "SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
                    " WHERE c.relname = 'analysis_runs' AND NOT t.tgisinternal"
                )
                triggers: list[str] = list(connection.execute(trigger_query).scalars())
                assert triggers == ["analysis_runs_guard"]
                jsonb: str = connection.execute(
                    text(
                        "SELECT data_type FROM information_schema.columns"
                        " WHERE table_name = 'analysis_runs' AND column_name = 'parameters'"
                    )
                ).scalar_one()
                assert jsonb == "jsonb"
            command.downgrade(config, "0003")
            with engine.connect() as connection:
                function_query = text(
                    "SELECT count(*) FROM pg_proc WHERE proname = 'veritas_analysis_runs_guard'"
                )
                assert connection.execute(function_query).scalar_one() == 0
            command.upgrade(config, "head")
        finally:
            engine.dispose()


def test_postgres_follows_the_ci_sequence_upgrade_seed_twice_downgrade_to_base() -> None:
    with scratch_postgres_database() as url:
        config = _config(url)
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            for _ in range(2):  # the seed is idempotent
                with Session(engine) as session, session.begin():
                    seed_demonstration_data(session)
            with engine.connect() as connection:
                count: int = connection.execute(text("SELECT count(*) FROM cases")).scalar_one()
            command.downgrade(config, "base")
            assert count >= 2
            assert "analysis_runs" not in inspect(engine).get_table_names()
        finally:
            engine.dispose()


def test_postgres_downgrade_works_even_when_runs_exist() -> None:
    """Test databases are disposable: a downgrade must not be blocked by V2.3 history."""
    with scratch_postgres_database() as url:
        config = _config(url)
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with Session(engine) as session, session.begin():
                seed_demonstration_data(session)
            command.downgrade(config, "0003")
            command.upgrade(config, "head")
        finally:
            engine.dispose()
