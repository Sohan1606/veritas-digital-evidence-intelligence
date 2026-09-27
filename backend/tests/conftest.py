"""Test fixtures.

Database: PostgreSQL when ``VERITAS_TEST_DATABASE_URL`` is set (CI), otherwise a temporary
SQLite file. The schema is always built by running the real Alembic migrations.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.migrations import alembic_config, upgrade_to_head
from app.db.session import build_engine
from app.main import create_app
from app.seed import seed_demonstration_data

# Tests are hermetic: they never read a developer's repository-root .env or exported
# VERITAS_* settings (only the explicit test database URL is honoured).
Settings.model_config["env_file"] = None
for _name in [n for n in os.environ if n.startswith("VERITAS_")]:
    if _name != "VERITAS_TEST_DATABASE_URL":
        os.environ.pop(_name)


def _database_url(tmp_dir: Path) -> str:
    url = os.environ.get("VERITAS_TEST_DATABASE_URL")
    if url:
        return url
    return f"sqlite:///{tmp_dir / 'veritas-test.sqlite3'}"


def make_settings(database_url: str, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": database_url,
        "environment": "test",
        "access_mode": "demo",
        "allowed_hosts": ["testserver", "localhost"],
        "cors_origins": ["http://localhost:5173"],
        "log_level": "WARNING",
    }
    values.update(overrides)
    return Settings.model_validate(values)


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    url = _database_url(tmp_path_factory.mktemp("db"))
    config = alembic_config(url)
    config.attributes["configure_logger"] = False
    from alembic import command

    if url.startswith("postgresql"):
        command.downgrade(config, "base")  # start from an empty schema
    upgrade_to_head(url)

    engine = create_engine(url)
    with Session(engine) as session, session.begin():
        seed_demonstration_data(session)
    engine.dispose()
    return url


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    engine = build_engine(make_settings(database_url))
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    """A session whose work is always rolled back, leaving the seeded database untouched."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def app(database_url: str) -> FastAPI:
    return create_app(make_settings(database_url))


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
