"""The Method contract: what an executable examination Method is, and what it may do.

A Method is a versioned, code-owned, deterministic definition. It is given a bounded stream of
the PRESERVED bytes of one EvidenceObject (never a path, storage key or handle) and returns
neutral statements that become Observations. Anything a Method needs to be allowed to do is
listed here; anything not listed (files, shells, URLs, other evidence, the database) is simply
not reachable from the arguments it receives.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from app.domain.enums import EvidenceType, ExaminationFailureCode

METHOD_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
METHOD_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+$")

# Every failure code has one fixed message. Failure text is therefore always sanitized: it can
# never carry a path, a storage key, evidence content, an exception message or a stack trace.
FAILURE_MESSAGES: dict[ExaminationFailureCode, str] = {
    ExaminationFailureCode.METHOD_UNAVAILABLE: (
        "The Method version for this run is not available in this deployment."
    ),
    ExaminationFailureCode.NOT_ELIGIBLE: (
        "The EvidenceObject was no longer eligible for examination when the run started."
    ),
    ExaminationFailureCode.EVIDENCE_UNAVAILABLE: (
        "The preserved EvidenceObject could not be read from private evidence storage."
    ),
    ExaminationFailureCode.INTEGRITY_MISMATCH: (
        "The bytes read for this run do not match the byte count and digests recorded at "
        "intake, so no Observations were published. This is an integrity comparison only."
    ),
    ExaminationFailureCode.RESOURCE_LIMIT_EXCEEDED: (
        "The run exceeded a resource limit of the Method, so no Observations were published."
    ),
    ExaminationFailureCode.EXECUTION_FAILED: (
        "The Method did not complete because of an internal error; no Observations were published."
    ),
}

# Observation statements and output descriptions state measurements, not conclusions. These
# stems are rejected in anything a Method publishes. (Method ``limitations`` are authored
# disclaimers that name what the Method does NOT determine, so they are exempt and pinned
# verbatim by tests instead.)
VERDICT_VOCABULARY = re.compile(
    r"\b(authentic\w*|fake\w*|forg\w*|manipulat\w*|suspicious|malicious|real)\b",
    re.IGNORECASE,
)


class ExaminationError(Exception):
    """Base of the controlled, internal examination outcomes. Never shown to a client."""


class MethodFailure(ExaminationError):
    """The run cannot produce a result. Carries the one failure code that describes why."""

    def __init__(self, code: ExaminationFailureCode) -> None:
        super().__init__(code.value)
        self.code = code


class ExecutionCancelled(ExaminationError):
    """A cancellation request was observed at a cooperative checkpoint."""


class ExecutionInterrupted(ExaminationError):
    """The worker is shutting down; the run is released rather than finished."""


class ClaimLost(ExaminationError):
    """Another worker owns the run now (it was recovered as stale). Nothing may be published."""


class ParameterValidationError(ValueError):
    """Parameters do not satisfy the Method's contract. Carries rule types, never input."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("Parameters do not satisfy the Method's parameter contract")
        self.problems = tuple(problems)


class EvidenceInput(Protocol):
    """The only way a Method touches evidence: the PRESERVED bytes, once, in bounded chunks."""

    def chunks(self) -> Iterator[bytes]: ...


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    max_object_bytes: int
    chunk_bytes: int
    max_runtime_seconds: int
    max_observations: int
    max_statement_chars: int


@dataclass(frozen=True, slots=True)
class OutputSpec:
    """One kind of Observation a Method publishes, in publication order."""

    key: str
    label: str
    definition: str


MethodExecutor = Callable[[EvidenceInput, BaseModel], tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class MethodDefinition:
    """A versioned executable examination definition. Once released, a (key, version) pair never
    changes meaning: behaviour changes ship as a new version (pinned by tests)."""

    key: str
    version: str
    name: str
    purpose: str
    supported_evidence_types: frozenset[EvidenceType]
    input_requirements: tuple[str, ...]
    parameters_model: type[BaseModel]
    outputs: tuple[OutputSpec, ...]
    limitations: tuple[str, ...]
    limits: ResourceLimits
    execute: MethodExecutor = field(repr=False, compare=False)
    enabled: bool = True
    deterministic: bool = True

    def __post_init__(self) -> None:
        if not METHOD_KEY_PATTERN.fullmatch(self.key) or len(self.key) > 64:
            raise ValueError(f"invalid Method key: {self.key!r}")
        if not METHOD_VERSION_PATTERN.fullmatch(self.version) or len(self.version) > 32:
            raise ValueError(f"invalid Method version: {self.version!r}")
        if not (self.name.strip() and self.purpose.strip() and self.outputs and self.limitations):
            raise ValueError("a Method needs a name, purpose, outputs and stated limitations")
        if not self.supported_evidence_types:
            raise ValueError("a Method must support at least one evidence type")
        if len({spec.key for spec in self.outputs}) != len(self.outputs):
            raise ValueError("Method output keys must be unique")
        if len(self.outputs) > self.limits.max_observations:
            raise ValueError("a Method declares more outputs than its observation limit")
        for text in (
            self.name,
            *self.input_requirements,
            *(f"{o.label} {o.definition}" for o in self.outputs),
        ):
            if VERDICT_VOCABULARY.search(text):
                raise ValueError("Method metadata must state measurements, not conclusions")

    @property
    def reference(self) -> str:
        return f"{self.key}@{self.version}"

    def describe(self) -> dict[str, Any]:
        """The Method's public contract as plain data (API projection and definition digest)."""
        return {
            "key": self.key,
            "version": self.version,
            "name": self.name,
            "purpose": self.purpose,
            "supported_evidence_types": sorted(t.value for t in self.supported_evidence_types),
            "input_requirements": list(self.input_requirements),
            "parameters": self.parameters_model.model_json_schema(),
            "outputs": [
                {"key": o.key, "label": o.label, "definition": o.definition} for o in self.outputs
            ],
            "limitations": list(self.limitations),
            "resource_limits": {
                "max_object_bytes": self.limits.max_object_bytes,
                "chunk_bytes": self.limits.chunk_bytes,
                "max_runtime_seconds": self.limits.max_runtime_seconds,
                "max_observations": self.limits.max_observations,
                "max_statement_chars": self.limits.max_statement_chars,
            },
            "deterministic": self.deterministic,
            "enabled": self.enabled,
        }

    def parameter_signature(self) -> list[list[object]]:
        """Library-independent description of the parameter contract (names, types, defaults)."""
        fields = self.parameters_model.model_fields
        return [
            [name, repr(field.annotation), field.is_required(), repr(field.default)]
            for name, field in sorted(fields.items())
        ] + [["extra", str(self.parameters_model.model_config.get("extra"))]]

    def digest(self) -> str:
        """SHA-256 of the canonical contract. Pinned by tests so a released version cannot drift.

        It covers everything a client can see except the JSON Schema text generated by pydantic,
        which is replaced by ``parameter_signature`` so a library upgrade cannot change it.
        """
        contract = {k: v for k, v in self.describe().items() if k != "parameters"}
        contract["parameter_signature"] = self.parameter_signature()
        canonical = json.dumps(contract, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_parameters(definition: MethodDefinition, raw: object) -> dict[str, Any]:
    """Validate ``raw`` against the Method's parameter contract and return the canonical form.

    Unknown parameters are rejected, types are strict, defaults are applied. The error names
    locations and rule types only, never the submitted values.
    """
    try:
        model = definition.parameters_model.model_validate(raw, strict=True)
    except ValidationError as exc:
        raise ParameterValidationError(sorted({str(err["type"]) for err in exc.errors()})) from None
    dumped: dict[str, Any] = model.model_dump(mode="json")
    return dumped


def assert_neutral_statements(definition: MethodDefinition, statements: Sequence[object]) -> None:
    """Enforce the output contract: right count, bounded text, measurements only."""
    if (
        len(statements) != len(definition.outputs)
        or len(statements) > definition.limits.max_observations
    ):
        raise MethodFailure(ExaminationFailureCode.EXECUTION_FAILED)
    for statement in statements:
        if (
            not isinstance(statement, str)
            or not statement.strip()
            or len(statement) > definition.limits.max_statement_chars
            or VERDICT_VOCABULARY.search(statement)
        ):
            raise MethodFailure(ExaminationFailureCode.EXECUTION_FAILED)
