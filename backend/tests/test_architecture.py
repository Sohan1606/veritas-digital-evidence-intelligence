"""Architecture guards: canonical entities, one identifier system, API shape, schema drift."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi import FastAPI
from sqlalchemy import Engine

from app.db.base import Base, public_id_prefixes

CANONICAL_PREFIXES = {
    "Case": "CASE",
    "Objective": "OBJ",
    "Evidence": "EVD",
    "EvidenceObject": "EOBJ",
    "EvidenceCustodyEvent": "CST",
    "AnalysisRun": "ANL",
    "Observation": "OBS",
    "Finding": "FND",
    "Claim": "CLM",
    "Assessment": "ASM",
    "CaseRelationship": "REL",
    "AuditEvent": "AUD",
    "Organization": "ORG",
    "OrganizationMembership": "MEM",
    "Role": "ROLE",
    "RoleAssignment": "RLA",
    "User": "USR",
    "UserSession": "SES",
}

CASE_SCOPED_TABLES = {
    "objectives",
    "evidence",
    "evidence_objects",
    "evidence_custody_events",
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
        if name in {"identifier_sequences", "login_attempts"}:
            continue
        assert "id" in table.c, name
        if name == "audit_events":
            assert {"occurred_at", "actor"} <= set(table.c.keys())
            assert "updated_at" not in table.c  # append-only
            continue
        if name == "user_sessions":
            assert {"created_at", "created_by", "expires_at", "revoked_at"} <= set(table.c.keys())
            assert "updated_at" not in table.c
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


@dataclass(frozen=True)
class ApiOperation:
    """One exposed API operation (a single method on a single path)."""

    path: str
    methods: frozenset[str]
    name: str


def _api_routes(app: FastAPI) -> list[ApiOperation]:
    """Every exposed API operation, read from the public OpenAPI document.

    ``app.routes`` cannot be used: current FastAPI versions keep included routers as lazy
    ``_IncludedRouter`` objects, so filtering it for ``APIRoute`` silently returns nothing and
    every guard below would pass without checking anything. The OpenAPI schema is the public,
    version-stable description of exactly what the application exposes.
    """
    operations: list[ApiOperation] = []
    for path, item in app.openapi()["paths"].items():
        for method, operation in item.items():
            operations.append(
                ApiOperation(path, frozenset({method.upper()}), operation["operationId"])
            )
    return operations


def test_route_guards_are_not_vacuous(app: FastAPI) -> None:
    """The route-iterating guards must see the real API, or they prove nothing."""
    operations = _api_routes(app)
    paths = {op.path for op in operations}
    assert len(operations) >= 30, f"only {len(operations)} operations were discovered"
    assert {"/health", "/ready", "/api/v1/system", "/api/v1/cases"} <= paths
    assert any(p.startswith("/api/v1/auth/") for p in paths)
    assert any(p.startswith("/api/v1/admin/") for p in paths)
    assert "/api/v1/cases/{case_id}/evidence/{evidence_id}/objects/{object_id}/verify" in paths
    # Distinct operations must have distinct identifiers (FastAPI derives them from the route).
    assert len({op.name for op in operations}) == len(operations)


def test_case_data_api_writes_are_limited_to_the_explicit_routes(app: FastAPI) -> None:
    """Case-scoped reads are GET-only except these explicit evidence and examination routes."""
    evidence = "/api/v1/cases/{case_id}/evidence"
    objects = f"{evidence}/{{evidence_id}}/objects"
    allowed_methods = {
        f"{evidence}/intake": {"POST"},
        objects: {"POST"},
        # V2.2 adds authorized retrieval (GET) beside the V2.1 upload (PUT) on one path.
        f"{objects}/{{object_id}}/content": {"GET", "PUT"},
        f"{objects}/{{object_id}}/finalize": {"POST"},
        # V2.2 independent integrity verification: observational, writes one AuditEvent only.
        f"{objects}/{{object_id}}/verify": {"POST"},
        # V2.3 examination: queue a run, cancel it, or retry it as a NEW run. Nothing else.
        "/api/v1/cases/{case_id}/analysis-runs": {"GET", "POST"},
        "/api/v1/cases/{case_id}/analysis-runs/{run_id}/cancel": {"POST"},
        "/api/v1/cases/{case_id}/analysis-runs/{run_id}/retry": {"POST"},
    }
    observed: dict[str, set[str]] = {}
    for route in _api_routes(app):
        methods = route.methods
        if route.path.startswith("/api/v1/cases"):
            observed.setdefault(route.path, set()).update(methods)
        elif route.path.startswith("/api/v1/auth"):
            assert methods <= {"GET", "POST"}, f"{route.path} exposes {methods}"
        elif route.path.startswith("/api/v1/admin"):
            assert methods <= {"GET", "POST", "PATCH", "DELETE"}, route.path
    for path, exposed in observed.items():
        expected = allowed_methods.get(path, {"GET"})
        assert exposed == expected, f"{path} exposes {exposed}, expected {expected}"
    assert set(allowed_methods) <= set(observed), "an expected evidence route is missing"
    # Nothing else may mutate: no PUT/PATCH/DELETE exists beside the upload route.
    mutating = {
        (method, path)
        for path, exposed in observed.items()
        for method in exposed
        if method in {"PUT", "PATCH", "DELETE"}
    }
    assert mutating == {("PUT", f"{objects}/{{object_id}}/content")}


def test_v21_spec_matches_api_route_and_has_a_complete_ending(app: FastAPI) -> None:
    spec_path = Path(__file__).resolve().parents[2] / "docs" / "v2.1-evidence-intake-spec.md"
    specification = spec_path.read_text(encoding="utf-8")
    canonical_route = "/api/v1/cases/{case_id}/evidence/intake"
    route_paths = set(app.openapi()["paths"])
    assert canonical_route in route_paths
    assert canonical_route in specification
    assert "/api/v1/cases/{case_id}/evidence-intake" not in specification
    assert "### Demonstration-mode data boundary" in specification
    assert specification.rstrip().endswith("post-V2.1 scope.")


def test_routes_are_versioned_unique_and_kebab_case(app: FastAPI) -> None:
    seen: set[tuple[str, str]] = set()
    for route in _api_routes(app):
        assert route.path in {"/health", "/ready"} or route.path.startswith("/api/v1/"), route.path
        for method in route.methods:
            assert (method, route.path) not in seen, f"duplicate route {method} {route.path}"
            seen.add((method, route.path))
        for segment in route.path.strip("/").split("/"):
            assert re.fullmatch(r"[a-z0-9-]+|\{[a-z_]+\}", segment), f"{route.path}: {segment}"


def test_case_data_routes_are_case_scoped(app: FastAPI) -> None:
    for route in _api_routes(app):
        if route.path.startswith("/api/v1/") and route.path != "/api/v1/system":
            assert route.path.startswith(("/api/v1/cases", "/api/v1/auth", "/api/v1/admin")), (
                route.path
            )


def test_no_forbidden_terminology_in_api_or_schema(app: FastAPI) -> None:
    names = [r.path for r in _api_routes(app)] + [r.name for r in _api_routes(app)]
    names += list(Base.metadata.tables)
    names += [c.name for t in Base.metadata.tables.values() for c in t.c]
    offenders = [n for n in names if FORBIDDEN_TERMS.search(n)]
    assert offenders == []
