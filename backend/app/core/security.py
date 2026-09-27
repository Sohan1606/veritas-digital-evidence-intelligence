"""Access boundary.

V1 deliberately contains NO authentication mechanism. This module defines the
interface every route uses to obtain a :class:`Principal` and to check case access,
so that V2 can replace the implementation (identity provider, RBAC, case permissions)
without touching route code.

Behaviour in V1 (fail closed):

* ``access_mode="restricted"`` (default): no principal can be established, so every
  case-data route responds ``401 authentication_unavailable``.
* ``access_mode="demo"``: an anonymous, read-only *demonstration viewer* principal is
  issued. It may read cases flagged ``is_demonstration`` and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from fastapi import Request

from app.core.config import Settings
from app.core.errors import AuthenticationUnavailableError

PrincipalKind = Literal["demonstration_viewer"]

DEMO_READ = "demonstration:read"


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    kind: PrincipalKind
    authenticated: bool
    permissions: frozenset[str] = field(default_factory=frozenset)


DEMONSTRATION_VIEWER = Principal(
    subject="anonymous-demonstration-viewer",
    kind="demonstration_viewer",
    authenticated=False,
    permissions=frozenset({DEMO_READ}),
)


def resolve_principal(settings: Settings) -> Principal:
    if settings.access_mode == "demo":
        return DEMONSTRATION_VIEWER
    raise AuthenticationUnavailableError(
        "Authentication is not available in this version; case data cannot be accessed"
    )


def get_principal(request: Request) -> Principal:
    """FastAPI dependency. V2 replaces the body with real identity resolution."""
    settings: Settings = request.app.state.settings
    return resolve_principal(settings)


def can_read_case(principal: Principal, *, is_demonstration: bool) -> bool:
    """Case-level read authorization. V2 adds per-case permissions here.

    Callers must treat an unreadable case exactly like a nonexistent one (404), so that
    the existence of restricted cases is never disclosed.
    """
    return is_demonstration and DEMO_READ in principal.permissions


def readable_demonstration_only(principal: Principal) -> bool:
    """True when the principal may only ever see demonstration cases (all V1 principals)."""
    return principal.kind == "demonstration_viewer"
