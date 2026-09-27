"""Case Knowledge Graph projection.

One graph per case, built from canonical entities: structural edges are projected from
ownership, asserted edges come from ``case_relationships``. All graph views (visual,
textual, future filtered views) consume this single projection.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import NodeType, RelationshipType
from app.domain.models import Case, CaseRelationship, Claim, Evidence, Finding, Observation
from app.schemas import CaseGraph, GraphNode, GraphRelationship


def _truncate(text: str, limit: int = 96) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_case_graph(session: Session, case: Case) -> CaseGraph:
    nodes: list[GraphNode] = [
        GraphNode(id=case.public_id, type=NodeType.CASE, label=case.title, state=case.state.value)
    ]
    public_ids: dict[uuid.UUID, str] = {case.id: case.public_id}
    relationships: list[GraphRelationship] = []

    def structural(source: str, rel: RelationshipType, target: str) -> None:
        relationships.append(
            GraphRelationship(
                id=f"structural:{source}:{rel.value}:{target}",
                origin="structural",
                type=rel,
                source=source,
                target=target,
                rationale=None,
            )
        )

    for e in session.execute(
        select(Evidence).where(Evidence.case_id == case.id).order_by(Evidence.created_at)
    ).scalars():
        public_ids[e.id] = e.public_id
        nodes.append(
            GraphNode(id=e.public_id, type=NodeType.EVIDENCE, label=e.label, state=e.state.value)
        )
        structural(case.public_id, RelationshipType.CONTAINS, e.public_id)

    for o in session.execute(
        select(Observation).where(Observation.case_id == case.id).order_by(Observation.created_at)
    ).scalars():
        public_ids[o.id] = o.public_id
        nodes.append(
            GraphNode(
                id=o.public_id,
                type=NodeType.OBSERVATION,
                label=_truncate(o.statement),
                state=o.state.value,
            )
        )
        structural(o.public_id, RelationshipType.DERIVED_FROM, public_ids[o.evidence_id])

    for f in session.execute(
        select(Finding).where(Finding.case_id == case.id).order_by(Finding.created_at)
    ).scalars():
        public_ids[f.id] = f.public_id
        nodes.append(
            GraphNode(
                id=f.public_id, type=NodeType.FINDING, label=f.title, state=f.review_status.value
            )
        )

    for c in session.execute(
        select(Claim).where(Claim.case_id == case.id).order_by(Claim.created_at)
    ).scalars():
        public_ids[c.id] = c.public_id
        nodes.append(
            GraphNode(
                id=c.public_id,
                type=NodeType.CLAIM,
                label=_truncate(c.statement),
                state=c.state.value,
            )
        )
        structural(case.public_id, RelationshipType.CONTAINS, c.public_id)

    for edge in session.execute(
        select(CaseRelationship)
        .where(CaseRelationship.case_id == case.id)
        .order_by(CaseRelationship.created_at)
    ).scalars():
        source, target = public_ids.get(edge.source_id), public_ids.get(edge.target_id)
        if source is None or target is None:
            continue  # dangling references are excluded rather than invented
        relationships.append(
            GraphRelationship(
                id=edge.public_id,
                origin="asserted",
                type=edge.relationship_type,
                source=source,
                target=target,
                rationale=edge.rationale,
            )
        )

    return CaseGraph(case_id=case.public_id, nodes=nodes, relationships=relationships)
