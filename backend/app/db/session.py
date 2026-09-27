"""Engine and session management."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings


def build_engine(settings: Settings) -> Engine:
    url = settings.database_url.get_secret_value()
    if url.startswith("sqlite"):
        engine = create_engine(url, echo=settings.database_echo)

        @event.listens_for(engine, "connect")
        def _sqlite_fk(dbapi_connection, _record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    return create_engine(
        url,
        echo=settings.database_echo,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"connect_timeout": 5},
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session
