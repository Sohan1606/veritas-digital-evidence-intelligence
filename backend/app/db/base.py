"""Declarative base, shared mixins and the single human-readable identifier system.

Identifier policy
-----------------
* Every persistent entity has an internal UUID primary key that never leaves the backend.
* Every persistent entity that users refer to has exactly one human-readable identifier,
  ``<PREFIX>-<n>`` zero-padded to three digits (``CASE-001``, ``EVD-012``, ``FND-1000``).
* Prefixes are declared once per model via ``__public_id_prefix__`` and numbers are allocated
  atomically from the ``identifier_sequences`` table. They are unique per deployment and are
  never reused.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, ClassVar, cast

from sqlalchemy import Integer, MetaData, String, Table, Uuid, event, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.db.types import JSONDocument, UTCDateTime, utcnow

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ACTOR_LENGTH = 128
PUBLIC_ID_LENGTH = 24


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: UTCDateTime(),
        dict[str, Any]: JSONDocument,
        list[Any]: JSONDocument,
        uuid.UUID: Uuid(),
    }


class IdentifierSequence(Base):
    """Allocation state for human-readable identifiers (one row per prefix)."""

    __tablename__ = "identifier_sequences"

    prefix: Mapped[str] = mapped_column(String(8), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False)


class PublicIdMixin:
    __public_id_prefix__: ClassVar[str]

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    public_id: Mapped[str] = mapped_column(String(PUBLIC_ID_LENGTH), unique=True, index=True)


class TimestampedMixin:
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    # Actor references. V1 actors are system processes (e.g. "system:demo-seed");
    # V2 binds these to authenticated principal subjects.
    created_by: Mapped[str] = mapped_column(String(ACTOR_LENGTH))
    updated_by: Mapped[str] = mapped_column(String(ACTOR_LENGTH))


def format_public_id(prefix: str, number: int) -> str:
    return f"{prefix}-{number:03d}"


def allocate_public_id(session: Session, prefix: str) -> str:
    """Atomically reserve the next identifier for ``prefix``.

    Uses a single upsert statement, so it is safe under concurrency on PostgreSQL.
    """
    connection = session.connection()
    dialect = connection.dialect.name
    insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert
    table = cast(Table, IdentifierSequence.__table__)
    stmt = (
        insert_fn(table)
        .values(prefix=prefix, last_value=1)
        .on_conflict_do_update(
            index_elements=[table.c.prefix],
            set_={"last_value": table.c.last_value + 1},
        )
        .returning(table.c.last_value)
    )
    number: int = connection.execute(stmt).scalar_one()
    return format_public_id(prefix, number)


@event.listens_for(Session, "before_flush")
def _assign_public_ids(session: Session, _flush_context: Any, _instances: Any) -> None:
    for obj in list(session.new):
        if isinstance(obj, PublicIdMixin) and not getattr(obj, "public_id", None):
            obj.public_id = allocate_public_id(session, type(obj).__public_id_prefix__)


def public_id_prefixes() -> dict[str, str]:
    """Mapping of model class name -> identifier prefix, derived from the ORM registry."""
    result: dict[str, str] = {}
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if issubclass(cls, PublicIdMixin):
            result[cls.__name__] = cls.__public_id_prefix__
    return result


def current_sequence_value(session: Session, prefix: str) -> int:
    value = session.execute(
        select(IdentifierSequence.last_value).where(IdentifierSequence.prefix == prefix)
    ).scalar_one_or_none()
    return value or 0
