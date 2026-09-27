"""Alembic integration helpers (used by readiness checks, tests and tooling)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def alembic_config(database_url: str | None = None) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    if database_url:
        # ConfigParser interpolation: escape '%' in URL-encoded passwords.
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def expected_head() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def upgrade_to_head(database_url: str) -> None:
    command.upgrade(alembic_config(database_url), "head")
