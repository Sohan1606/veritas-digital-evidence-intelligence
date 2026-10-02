"""The Method registry: the single authoritative, code-owned list of executable Methods.

Method behaviour is never stored in the database and cannot be edited at runtime. A released
(key, version) is immutable; a change in behaviour or contract is a new version.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.examination.contracts import MethodDefinition
from app.examination.methods.binary_characteristics import BINARY_CHARACTERISTICS


class MethodRegistry:
    def __init__(self, definitions: Iterable[MethodDefinition]) -> None:
        self._definitions: dict[tuple[str, str], MethodDefinition] = {}
        for definition in definitions:
            identity = (definition.key, definition.version)
            if identity in self._definitions:
                raise ValueError(f"Method {definition.reference} is registered twice")
            self._definitions[identity] = definition

    def get(self, key: str, version: str) -> MethodDefinition | None:
        return self._definitions.get((key, version))

    def all(self) -> tuple[MethodDefinition, ...]:
        return tuple(self._definitions[identity] for identity in sorted(self._definitions))


METHOD_REGISTRY = MethodRegistry([BINARY_CHARACTERISTICS])
