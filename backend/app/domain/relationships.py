"""Case Knowledge Graph relationship rules.

There is exactly one graph per case. Its edges come from two origins:

* **structural** — projected from ownership foreign keys (never stored as edges):
  ``Case contains Evidence``, ``Case contains Claim``, ``Observation derived-from Evidence``.
* **asserted** — stored in ``case_relationships`` and validated against
  :data:`ASSERTED_RELATIONSHIPS` below.
"""

from __future__ import annotations

from typing import Final

from app.domain.enums import NodeType as N
from app.domain.enums import RelationshipType as R

ASSERTED_RELATIONSHIPS: Final[frozenset[tuple[N, R, N]]] = frozenset(
    {
        (N.FINDING, R.DERIVED_FROM, N.OBSERVATION),
        (N.FINDING, R.DERIVED_FROM, N.FINDING),
        (N.FINDING, R.SUPPORTS, N.CLAIM),
        (N.FINDING, R.CONTRADICTS, N.CLAIM),
        (N.FINDING, R.CONTRADICTS, N.FINDING),
        (N.CLAIM, R.CONTRADICTS, N.CLAIM),
        (N.CLAIM, R.REFERENCES, N.EVIDENCE),
    }
)

STRUCTURAL_RELATIONSHIPS: Final[frozenset[tuple[N, R, N]]] = frozenset(
    {
        (N.CASE, R.CONTAINS, N.EVIDENCE),
        (N.CASE, R.CONTAINS, N.CLAIM),
        (N.OBSERVATION, R.DERIVED_FROM, N.EVIDENCE),
    }
)


def is_assertable(source: N, relationship: R, target: N) -> bool:
    return (source, relationship, target) in ASSERTED_RELATIONSHIPS
