"""Analysis Run lifecycle: the single owner of which state changes are permitted.

The coordinator, the ORM guard in ``domain.models`` and the PostgreSQL trigger created by
migration 0004 all enforce this one table. A run is forensic execution history: finished runs
are final, and a retry is always a NEW run (never a state change of the old one).

``RUNNING -> QUEUED`` is not an execution outcome. It exists only so that a run whose worker
stopped (stale heartbeat, or a graceful shutdown) is executed again by a healthy worker.
"""

from __future__ import annotations

from app.domain.enums import AnalysisRunState

S = AnalysisRunState

RUN_TRANSITIONS: dict[AnalysisRunState, frozenset[AnalysisRunState]] = {
    S.QUEUED: frozenset({S.RUNNING, S.CANCELLED}),
    S.RUNNING: frozenset({S.COMPLETED, S.FAILED, S.CANCELLED, S.QUEUED}),
    S.COMPLETED: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
}

TERMINAL_RUN_STATES: frozenset[AnalysisRunState] = frozenset(
    state for state, targets in RUN_TRANSITIONS.items() if not targets
)

# States a retry may copy: the run ended without producing a completed result.
RETRYABLE_RUN_STATES: frozenset[AnalysisRunState] = frozenset({S.FAILED, S.CANCELLED})


class InvalidRunTransitionError(RuntimeError):
    """Raised when a state change is not in ``RUN_TRANSITIONS``."""


def is_valid_transition(current: AnalysisRunState, target: AnalysisRunState) -> bool:
    return target in RUN_TRANSITIONS[current]


def require_transition(current: AnalysisRunState, target: AnalysisRunState) -> None:
    if not is_valid_transition(current, target):
        raise InvalidRunTransitionError(
            f"an analysis run cannot change from '{current.value}' to '{target.value}'"
        )
