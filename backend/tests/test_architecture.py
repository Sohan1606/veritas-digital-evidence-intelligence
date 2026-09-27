"""Architecture guards: canonical entities, one identifier system, API shape, schema drift."""

from __future__ import annotations

import re

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import Engine

from app.db.base import Base, public_id_prefixes

CANONICAL_PREFIXES = {
    "Case": "CASE",
    "Objective": "OBJ",
    "Evidence": "EVD",
    "AnalysisRun": "ANL",
    "Observation": "OBS",
    "Finding": "FND",
    "Claim": "CLM",
    "Assessment": "ASM",
    "CaseRelationship": "REL",
    "AuditEvent": "AUD",
}

CASE_SCOPED_TABLES = {
    "objectives",
    "evidence",
    "analysis_runs",
    "observations",
    "findings",
    "claims",
    "assessments",
    "case_relationships",
}

FORBIDDEN_TERMS = re.compile(
    r"truth[_\- ]?(score|engine)|trust[_\- ]?engine|reality[_\- ]?engine|ai[_\- ]?brain|"
    r"deep[_\- ]?scan|smart[_\- ]?scan|magic|evidence[_\- ]?(health|trust|readiness|quality)|"
    r"(evidence|claim|dependency|relationship)[_\- ]?graph",
    re.IGNORECASE,
)


def test_canonical_entities_and_single_identifier_system() -> None:
    assert public_id_prefixes() == CANONICAL_PREFIXES
    assert len(set(CANONICAL_PREFIXES.values())) == len(CANONICAL_PREFIXES)


def test_evidence_profile_is_owned_by_evidence() -> None:
    table = Base.metadata.tables["evidence_profiles"]
    assert "public_id" not in table.c  # identified through its Evidence; no second ID system
    assert table.c.evidence_id.unique
    assert {
        "identity",
        "integrity",
        "provenance",
        "quality",
        "acquisition_context",
        "classification",
    } <= set(table.c.keys())


def test_persistent_entities_have_ids_timestamps_and_actors() -> None:
    for name, table in Base.metadata.tables.items():
        if name == "identifier_sequences":
            continue
        assert "id" in table.c, name
        if name == "audit_events":
            assert {"occurred_at", "actor"} <= set(table.c.keys())
            assert "updated_at" not in table.c  # append-only
            continue
        assert {"created_at", "updated_at", "created_by", "updated_by"} <= set(table.c.keys()), name


def test_case_scoped_entities_reference_case_and_never_cascade() -> None:
    for name in CASE_SCOPED_TABLES:
        table = Base.metadata.tables[name]
        fks = {fk.column.table.name: fk for fk in table.c.case_id.foreign_keys}
        assert "cases" in fks, name
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            assert fk.ondelete == "RESTRICT", f"{table.name}.{fk.parent.name}"


def test_migrations_match_models(engine: Engine) -> None:
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == [], f"models and migrations have drifted: {diff}"


def _api_routes(app: FastAPI) -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute)]


def test_api_is_read_only_in_v1(app: FastAPI) -> None:
    for route in _api_routes(app):
        assert route.methods == {"GET"}, f"{route.path} exposes {route.methods}"


def test_routes_are_versioned_unique_and_kebab_case(app: FastAPI) -> None:
    seen: set[tuple[str, str]] = set()
    for route in _api_routes(app):
        assert route.path in {"/health", "/ready"} or route.path.startswith("/api/v1/"), route.path
        for method in route.methods or set():
            assert (method, route.path) not in seen, f"duplicate route {method} {route.path}"
            seen.add((method, route.path))
        for segment in route.path.strip("/").split("/"):
            assert re.fullmatch(r"[a-z0-9-]+|\{[a-z_]+\}", segment), f"{route.path}: {segment}"


def test_case_data_routes_are_case_scoped(app: FastAPI) -> None:
    for route in _api_routes(app):
        if route.path.startswith("/api/v1/") and route.path != "/api/v1/system":
            assert route.path.startswith("/api/v1/cases"), route.path


def test_no_forbidden_terminology_in_api_or_schema(app: FastAPI) -> None:
    names = [r.path for r in _api_routes(app)] + [r.name for r in _api_routes(app)]
    names += list(Base.metadata.tables)
    names += [c.name for t in Base.metadata.tables.values() for c in t.c]
    offenders = [n for n in names if FORBIDDEN_TERMS.search(n)]
    assert offenders == []
