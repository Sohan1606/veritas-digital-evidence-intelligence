"""Development seed: DEMONSTRATION DATA — NOT REAL EVIDENCE.

Creates two fictional cases through the regular domain commands (so every record has a
genuine audit trail showing it was created by this seed). No real persons, events,
files or evidence are represented. No evidence content exists: evidence items are
metadata records only, and every "observation" is a manually recorded demonstration
statement — no examination method has been executed.

Usage:  python -m app.seed            (idempotent: does nothing if demonstration data exists)
"""

from __future__ import annotations

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import build_engine, build_session_factory
from app.domain.enums import AlternativeExplanationStatus as Alt
from app.domain.enums import AttributeBasis, EvidenceType, ProfileStatus
from app.domain.enums import RelationshipType as R
from app.domain.models import Case, Evidence
from app.domain.values import AlternativeExplanation, ProfileAttribute, ProfileSection
from app.schemas import DEMONSTRATION_NOTICE
from app.services import records

SEED_ACTOR = "system:demo-seed"
DEMO_INVESTIGATOR = "demo:synthetic-investigator"

D = AttributeBasis.DECLARED

INTEGRITY_NOT_AVAILABLE = ProfileSection(
    status=ProfileStatus.NOT_AVAILABLE,
    note="No evidence content has been ingested and V1 has no hashing pipeline; integrity cannot be established.",
)
QUALITY_NOT_AVAILABLE = ProfileSection(
    status=ProfileStatus.NOT_AVAILABLE,
    note="No examination method has assessed this evidence; quality is not established in V1.",
)


def _declared(*pairs: tuple[str, str, str]) -> tuple[ProfileAttribute, ...]:
    return tuple(ProfileAttribute(key=k, label=label, value=v, basis=D) for k, label, v in pairs)


def _profile(
    *,
    filename: str,
    media_type: str,
    size: str,
    source: str,
    acquisition: str,
    acquired: str,
    kind: str,
) -> dict[str, ProfileSection]:
    return {
        "identity": ProfileSection(
            status=ProfileStatus.PARTIAL,
            attributes=_declared(
                ("filename", "Declared filename", filename),
                ("media_type", "Declared media type", media_type),
                ("size", "Declared size", size),
            ),
            note="Declared by the submitter. No content exists to check these values against.",
        ),
        "integrity": INTEGRITY_NOT_AVAILABLE,
        "provenance": ProfileSection(
            status=ProfileStatus.PARTIAL,
            attributes=(
                *_declared(("submitted_by", "Submitted by", source)),
                ProfileAttribute(
                    key="origin_device", label="Originating device", value=None, basis=D
                ),
            ),
            note="Originating device was not declared.",
        ),
        "quality": QUALITY_NOT_AVAILABLE,
        "acquisition_context": ProfileSection(
            status=ProfileStatus.PARTIAL,
            attributes=_declared(
                ("method", "Declared acquisition method", acquisition),
                ("acquired_on", "Declared acquisition date", acquired),
            ),
            note="Acquisition details are declared, not recorded by an acquisition procedure.",
        ),
        "classification": ProfileSection(
            status=ProfileStatus.PARTIAL,
            attributes=_declared(
                ("evidence_kind", "Declared evidence kind", kind),
                ("data_origin", "Data origin", "Synthetic demonstration record"),
            ),
        ),
    }


def demonstration_data_exists(session: Session) -> bool:
    return (
        session.execute(select(Case.id).where(Case.is_demonstration.is_(True)).limit(1)).first()
        is not None
    )


def seed_demonstration_data(session: Session) -> None:
    a = SEED_ACTOR
    note = f"{DEMONSTRATION_NOTICE}. Fictional scenario; no real persons, events or evidence are represented."

    # ---- CASE-001 ------------------------------------------------------------------------
    case = records.create_case(
        session,
        actor=a,
        title="Synthetic Demonstration Case — Disputed delivery sequence",
        summary=(
            f"{note} A recipient disputes that a parcel was delivered at the time stated by a courier. "
            "Evidence items are metadata records only; observations were recorded manually by the seed."
        ),
        is_demonstration=True,
    )
    records.add_objective(
        session,
        actor=a,
        case=case,
        statement="Determine whether the registered materials are consistent with the claimed delivery sequence on 14 March 2026.",
    )
    records.add_objective(
        session,
        actor=a,
        case=case,
        statement="Identify the open questions that require examination before any claim can be assessed.",
    )

    photo = records.register_evidence(
        session,
        actor=a,
        case=case,
        label="delivery_photo.jpg",
        evidence_type=EvidenceType.IMAGE,
        description="Photograph said to show the parcel at the recipient's door. Synthetic metadata record; no image content exists.",
    )
    chat = records.register_evidence(
        session,
        actor=a,
        case=case,
        label="courier_chat_export.txt",
        evidence_type=EvidenceType.MESSAGE_EXPORT,
        description="Export of a messaging conversation with the courier. Synthetic metadata record; no content exists.",
    )
    email = records.register_evidence(
        session,
        actor=a,
        case=case,
        label="delivery_confirmation.eml",
        evidence_type=EvidenceType.EMAIL,
        description="Delivery confirmation email. Synthetic metadata record; no content exists.",
    )
    voicemail = records.register_evidence(
        session,
        actor=a,
        case=case,
        label="recipient_voicemail.m4a",
        evidence_type=EvidenceType.AUDIO,
        description="Voicemail left by the recipient. Synthetic metadata record; no Evidence Profile has been recorded.",
    )

    for evidence, sections in (
        (
            photo,
            _profile(
                filename="delivery_photo.jpg",
                media_type="image/jpeg",
                size="2.4 MB",
                source="Courier (synthetic party)",
                acquisition="Forwarded by messaging app",
                acquired="2026-03-15",
                kind="Photograph",
            ),
        ),
        (
            chat,
            _profile(
                filename="courier_chat_export.txt",
                media_type="text/plain",
                size="18 KB",
                source="Recipient (synthetic party)",
                acquisition="In-app conversation export",
                acquired="2026-03-16",
                kind="Message export",
            ),
        ),
        (
            email,
            _profile(
                filename="delivery_confirmation.eml",
                media_type="message/rfc822",
                size="41 KB",
                source="Recipient (synthetic party)",
                acquisition="Saved from mail client",
                acquired="2026-03-16",
                kind="Email message",
            ),
        ),
    ):
        records.set_evidence_profile(
            session, actor=a, case=case, evidence=evidence, sections=sections
        )

    obs_capture = records.record_observation(
        session,
        actor=a,
        case=case,
        evidence=photo,
        statement="Declared capture time recorded with the photograph is 14 Mar 2026 18:42.",
    )
    obs_message = records.record_observation(
        session,
        actor=a,
        case=case,
        evidence=chat,
        statement="The courier message 'Parcel delivered' carries the timestamp 14 Mar 2026 21:31 (+05:30).",
    )
    obs_email = records.record_observation(
        session,
        actor=a,
        case=case,
        evidence=email,
        statement="The confirmation email Date header states 14 Mar 2026 21:34 +0530.",
    )
    obs_no_offset = records.record_observation(
        session,
        actor=a,
        case=case,
        evidence=photo,
        statement="The declared capture-time record contains no timezone or UTC offset.",
    )

    claim_photo = records.record_claim(
        session,
        actor=a,
        case=case,
        source="Courier statement (synthetic)",
        statement="The photograph was taken at the recipient's door at the moment of delivery.",
    )
    claim_time = records.record_claim(
        session,
        actor=a,
        case=case,
        source="Courier statement (synthetic)",
        statement="The parcel was delivered at about 21:30 on 14 March 2026.",
    )
    claim_none = records.record_claim(
        session,
        actor=a,
        case=case,
        source="Recipient statement (synthetic)",
        statement="No delivery took place on 14 March 2026.",
    )

    fnd_gap = records.record_finding(
        session,
        actor=a,
        case=case,
        title="Declared photo capture time precedes the delivery message by 2 h 49 min",
        statement=(
            "Read in the same local time, the capture time declared for EVD-001 (18:42) is 2 hours 49 minutes "
            "earlier than the 'Parcel delivered' message in EVD-002 (21:31 +05:30)."
        ),
        method="Manual comparison of declared timestamps. Demonstration content — no automated method was executed.",
        limitations=[
            "Timestamps are declared values in synthetic records; nothing was extracted from evidence content.",
            "EVD-001 records no timezone, so the comparison assumes both values share local time.",
            "Clock accuracy of both originating devices is unknown.",
        ],
        alternative_explanations=[
            AlternativeExplanation(
                explanation="The camera clock was set to UTC; 18:42 UTC would be 00:12 (+05:30) on 15 March, after the delivery message."
            ),
            AlternativeExplanation(
                explanation="The photograph was taken earlier (for example at a depot) and shared later."
            ),
            AlternativeExplanation(
                explanation="An application re-saved the image and rewrote the declared capture time."
            ),
        ],
    )
    fnd_consistent = records.record_finding(
        session,
        actor=a,
        case=case,
        title="Delivery message and confirmation email are three minutes apart",
        statement=(
            "The 'Parcel delivered' message (21:31 +05:30) and the confirmation email Date header (21:34 +0530) "
            "are consistent with a single delivery-related event at about 21:30."
        ),
        method="Manual comparison of declared timestamps. Demonstration content — no automated method was executed.",
        limitations=[
            "Email Date headers are set by the sending client and can be wrong or altered.",
            "Both records may originate from the same courier system, so they may not be independent.",
        ],
        alternative_explanations=[
            AlternativeExplanation(
                explanation="Both messages were generated automatically by a dispatch system without a physical delivery."
            ),
            AlternativeExplanation(
                explanation="The two records refer to different days.",
                status=Alt.EXCLUDED,
                basis="Both OBS-002 and OBS-003 record 14 March 2026 with the same UTC offset.",
            ),
        ],
    )

    for source, rel, target, rationale in (
        (fnd_gap, R.DERIVED_FROM, obs_capture, None),
        (fnd_gap, R.DERIVED_FROM, obs_message, None),
        (fnd_gap, R.DERIVED_FROM, obs_no_offset, "Establishes the missing-offset limitation."),
        (fnd_consistent, R.DERIVED_FROM, obs_message, None),
        (fnd_consistent, R.DERIVED_FROM, obs_email, None),
        (
            fnd_gap,
            R.CONTRADICTS,
            claim_photo,
            "Only if both timestamps share a timezone; see alternative explanations.",
        ),
        (
            fnd_consistent,
            R.SUPPORTS,
            claim_time,
            "Both records are courier-originated and may not be independent.",
        ),
        (claim_none, R.CONTRADICTS, claim_time, None),
        (claim_photo, R.REFERENCES, photo, None),
        (claim_time, R.REFERENCES, chat, None),
        (claim_time, R.REFERENCES, email, None),
        (claim_none, R.REFERENCES, voicemail, None),
    ):
        records.assert_relationship(
            session,
            actor=a,
            case=case,
            source=source,
            relationship_type=rel,
            target=target,
            rationale=rationale,
        )

    records.record_assessment(
        session,
        actor=DEMO_INVESTIGATOR,
        case=case,
        claim=claim_time,
        statement=(
            "Draft — no conclusion recorded. FND-002 is unreviewed and the independence of EVD-002 and EVD-003 "
            "is an open question."
        ),
    )

    # ---- CASE-002: demonstrates empty states --------------------------------------------
    empty = records.create_case(
        session,
        actor=a,
        title="Synthetic Demonstration Case — Awaiting intake",
        summary=f"{note} A case with an objective but no registered evidence.",
        is_demonstration=True,
    )
    records.add_objective(
        session,
        actor=a,
        case=empty,
        statement="Establish the origin of a disputed document (fictional).",
    )


def main() -> int:
    settings = get_settings()
    if settings.environment == "production":
        print("Refusing to seed demonstration data in a production environment.", file=sys.stderr)
        return 2
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        if demonstration_data_exists(session):
            print("Demonstration data already present; nothing to do.")
            return 0
        seed_demonstration_data(session)
        count = session.execute(select(Evidence.id)).all()
    print(f"Seeded {DEMONSTRATION_NOTICE} ({len(count)} evidence records).")
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
