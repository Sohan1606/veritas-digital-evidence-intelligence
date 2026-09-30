"""Database URL and separate libpq password configuration regression tests."""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.db.session import build_engine


def test_compose_connection_keeps_reserved_password_out_of_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Construct an ephemeral test-only credential; never print or persist it.
    password = f"test-{uuid4().hex}@:/?#%{uuid4().hex}"
    monkeypatch.setenv("PGPASSWORD", password)

    database_url = "postgresql+psycopg://veritas@db:5432/veritas"
    engine = build_engine(Settings(environment="test", database_url=database_url))
    try:
        # Check the SQLAlchemy URL the psycopg dialect will use. Reserved password
        # characters stay solely in libpq's environment input, not the host portion.
        assert engine.url.host == "db"
        assert engine.url.port == 5432
        assert engine.url.username == "veritas"
        assert engine.url.database == "veritas"
        assert engine.url.password is None
        connection_args, connection_kwargs = engine.dialect.create_connect_args(engine.url)
        assert not connection_args
        assert connection_kwargs["host"] == "db"
        assert connection_kwargs["user"] == "veritas"
        assert connection_kwargs["dbname"] == "veritas"
        assert "password" not in connection_kwargs
        assert hmac.compare_digest(os.environ.get("PGPASSWORD", ""), password)

        compose_path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
        compose = compose_path.read_text(encoding="utf-8")
        assert f"      VERITAS_DATABASE_URL: {database_url}" in compose
        password_interpolation = "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}"
        assert f"      POSTGRES_PASSWORD: {password_interpolation}" in compose
        assert f"      PGPASSWORD: {password_interpolation}" in compose
        assert "      - pgdata:/var/lib/postgresql/data" in compose
        assert "      - evidencedata:/var/lib/veritas/evidence" in compose
    finally:
        engine.dispose()
