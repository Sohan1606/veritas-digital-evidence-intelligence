"""Domain rules: identifiers, profile semantics, relationships, audit immutability."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import DomainRuleViolation
from app.db.base import allocate_public_id, format_public_id
from app.domain.enums import (
    AlternativeExplanationStatus,
    AttributeBasis,
    EvidenceType,
    ProfileStatus,
)
from app.domain.enums import RelationshipType as R
from app.domain.models import AuditEvent, AuditEventImmutableError, Case, Observation
from app.domain.values import AlternativeExplanation, ProfileAttribute, ProfileSection
from app.seed import demonstration_data_exists
from app.services import records

ACTOR = "test:domain"


def _case(db: Session, title: str = "Domain test case") -> Case:
    return records.create_case(db, actor=ACTOR, title=title, summary=None, is_demonstration=False)


# --- Identifiers ------------------------------------------------------------------------


def test_public_id_format() -> None:
    assert format_public_id("CASE", 1) == "CASE-001"
    assert format_public_id("FND", 42) == "FND-042"
    assert format_public_id("EVD", 1000) == "EVD-1000"


def test_public_ids_are_sequential_per_prefix(db: Session) -> None:
    first = allocate_public_id(db, "TST")
    second = allocate_public_id(db, "TST")
    assert (first, second) == ("TST-001", "TST-002")


def test_entities_receive_public_ids_on_flush(db: Session) -> None:
    case = _case(db)
    evidence = records.register_evidence(
        db, actor=ACTOR, case=case, label="x", evidence_type=EvidenceType.OTHER, description=None
    )
    assert case.public_id.startswith("CASE-")
    assert evidence.public_id.startswith("EVD-")


# --- Evidence Profile semantics ---------------------------------------------------------


def _attr(basis: AttributeBasis, value: str | None = "v") -> ProfileAttribute:
    return ProfileAttribute(key="k", label="K", value=value, basis=basis)


def test_verified_requires_all_computed_values() -> None:
    ProfileSection(status=ProfileStatus.VERIFIED, attributes=(_attr(AttributeBasis.COMPUTED),))
    with pytest.raises(ValidationError, match="verified"):
        ProfileSection(status=ProfileStatus.VERIFIED, attributes=(_attr(AttributeBasis.DECLARED),))
    with pytest.raises(ValidationError, match="verified"):
        ProfileSection(status=ProfileStatus.VERIFIED, attributes=())


def test_partial_requires_a_recorded_value() -> None:
    with pytest.raises(ValidationError, match="partial"):
        ProfileSection(
            status=ProfileStatus.PARTIAL, attributes=(_attr(AttributeBasis.DECLARED, None),)
        )


@pytest.mark.parametrize("status", [ProfileStatus.UNKNOWN, ProfileStatus.NOT_AVAILABLE])
def test_unknown_and_not_available_carry_no_values_and_need_a_note(status: ProfileStatus) -> None:
    ProfileSection(status=status, note="explained")
    with pytest.raises(ValidationError):
        ProfileSection(status=status, attributes=(_attr(AttributeBasis.DECLARED),), note="x")
    with pytest.raises(ValidationError, match="note"):
        ProfileSection(status=status)


def test_profile_requires_all_six_sections(db: Session) -> None:
    case = _case(db)
    evidence = records.register_evidence(
        db, actor=ACTOR, case=case, label="x", evidence_type=EvidenceType.IMAGE, description=None
    )
    section = ProfileSection(status=ProfileStatus.NOT_AVAILABLE, note="n/a")
    with pytest.raises(DomainRuleViolation):
        records.set_evidence_profile(
            db, actor=ACTOR, case=case, evidence=evidence, sections={"identity": section}
        )


def test_excluded_alternative_requires_basis() -> None:
    with pytest.raises(ValidationError, match="basis"):
        AlternativeExplanation(explanation="x", status=AlternativeExplanationStatus.EXCLUDED)


def test_finding_requires_a_limitation(db: Session) -> None:
    with pytest.raises(DomainRuleViolation, match="limitation"):
        records.record_finding(
            db,
            actor=ACTOR,
            case=_case(db),
            title="t",
            statement="s",
            method="m",
            limitations=[],
            alternative_explanations=[],
        )


# --- Observations & relationships -------------------------------------------------------


def test_observation_origin_is_enforced_by_database(db: Session) -> None:
    case = _case(db)
    evidence = records.register_evidence(
        db, actor=ACTOR, case=case, label="x", evidence_type=EvidenceType.OTHER, description=None
    )
    obs = records.record_observation(db, actor=ACTOR, case=case, evidence=evidence, statement="s")
    assert obs.origin == "manual"
    db.add(
        Observation(
            case_id=case.id,
            evidence_id=evidence.id,
            origin="analysis_run",
            statement="s",
            created_by=ACTOR,
            updated_by=ACTOR,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_disallowed_relationship_is_rejected(db: Session) -> None:
    case = _case(db)
    claim = records.record_claim(db, actor=ACTOR, case=case, statement="c", source="s")
    evidence = records.register_evidence(
        db, actor=ACTOR, case=case, label="x", evidence_type=EvidenceType.OTHER, description=None
    )
    with pytest.raises(DomainRuleViolation, match="not a permitted relationship"):
        records.assert_relationship(
            db, actor=ACTOR, case=case, source=claim, relationship_type=R.SUPPORTS, target=evidence
        )


def test_cross_case_relationship_is_rejected(db: Session) -> None:
    case_a, case_b = _case(db, "A"), _case(db, "B")
    claim_a = records.record_claim(db, actor=ACTOR, case=case_a, statement="a", source="s")
    claim_b = records.record_claim(db, actor=ACTOR, case=case_b, statement="b", source="s")
    with pytest.raises(DomainRuleViolation, match="same case"):
        records.assert_relationship(
            db,
            actor=ACTOR,
            case=case_a,
            source=claim_a,
            relationship_type=R.CONTRADICTS,
            target=claim_b,
        )


def test_every_command_writes_an_audit_event(db: Session) -> None:
    case = _case(db)
    records.record_claim(db, actor=ACTOR, case=case, statement="c", source="s")
    actions = (
        db.execute(
            select(AuditEvent.action)
            .where(AuditEvent.case_id == case.id)
            .order_by(AuditEvent.occurred_at)
        )
        .scalars()
        .all()
    )
    assert actions == ["case.created", "claim.recorded"]


# --- Audit immutability -----------------------------------------------------------------


def test_audit_events_cannot_be_updated_through_orm(db: Session) -> None:
    event = db.execute(select(AuditEvent).limit(1)).scalar_one()
    event.action = "tampered"
    with pytest.raises(AuditEventImmutableError):
        db.flush()


def test_audit_events_cannot_be_deleted_through_orm(db: Session) -> None:
    event = db.execute(select(AuditEvent).limit(1)).scalar_one()
    db.delete(event)
    with pytest.raises(AuditEventImmutableError):
        db.flush()


def test_audit_events_append_only_in_postgresql(db: Session) -> None:
    if db.get_bind().dialect.name != "postgresql":
        pytest.skip("database-level trigger exists on PostgreSQL only")
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("UPDATE audit_events SET action = 'tampered'"))


def test_seed_is_detectable(db: Session) -> None:
    assert demonstration_data_exists(db)
