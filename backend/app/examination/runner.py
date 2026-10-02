"""Run one Method against a controlled, bounded evidence reader. No database access here.

``EvidenceReader`` is the only evidence-reading abstraction a Method ever sees. It exposes the
PRESERVED bytes once, in chunks of at most ``limits.chunk_bytes``; it never exposes a path,
storage key, file object or length-unbounded read. While the Method reads, the reader

* calls the coordinator's checkpoint (heartbeat, cancellation, shutdown) before every chunk,
* enforces the Method's byte and wall-clock limits, and
* feeds the canonical ``StreamingDigest`` so that, once the stream is exhausted, the bytes read
  can be compared with the byte count and digests recorded at intake.

That last step is what makes the provenance exact: Observations are only ever published for
bytes that still equal the recorded PRESERVED object. A mismatch fails the run (fail closed);
the full side-by-side comparison remains the job of V2.2 verification.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, BinaryIO

from app.domain.enums import ExaminationFailureCode
from app.examination.contracts import (
    MethodDefinition,
    MethodFailure,
    ParameterValidationError,
    ResourceLimits,
    assert_neutral_statements,
    validate_parameters,
)
from app.services.evidence_hashing import StreamingDigest, matches_recorded_integrity

Checkpoint = Callable[[], None]


@dataclass(frozen=True, slots=True)
class RecordedIntegrity:
    """The immutable values recorded when the object was preserved."""

    byte_size: int
    sha256: str
    sha512: str


class EvidenceReader:
    """Single-use, chunked, integrity-checked view of one PRESERVED object's bytes."""

    def __init__(
        self,
        stream: BinaryIO,
        *,
        recorded: RecordedIntegrity,
        limits: ResourceLimits,
        checkpoint: Checkpoint,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stream = stream
        self._recorded = recorded
        self._limits = limits
        self._checkpoint = checkpoint
        self._monotonic = monotonic
        self._deadline = monotonic() + limits.max_runtime_seconds
        self._digest = StreamingDigest()
        self._started = False
        self._exhausted = False

    def chunks(self) -> Iterator[bytes]:
        """Yield the object's bytes, never more than ``chunk_bytes`` at a time, exactly once."""
        if self._started:
            raise RuntimeError("the evidence stream can be read only once")
        self._started = True
        while True:
            self._checkpoint()
            if self._monotonic() > self._deadline:
                raise MethodFailure(ExaminationFailureCode.RESOURCE_LIMIT_EXCEEDED)
            try:
                chunk = self._stream.read(self._limits.chunk_bytes)
            except OSError:
                raise MethodFailure(ExaminationFailureCode.EVIDENCE_UNAVAILABLE) from None
            if not chunk:
                self._exhausted = True
                return
            if len(chunk) > self._limits.chunk_bytes:
                raise MethodFailure(ExaminationFailureCode.EXECUTION_FAILED)
            self._digest.update(chunk)
            if self._digest.byte_size > self._limits.max_object_bytes:
                raise MethodFailure(ExaminationFailureCode.RESOURCE_LIMIT_EXCEEDED)
            yield chunk

    def verify_integrity(self) -> None:
        """Require that the whole object was read and equals the recorded intake values."""
        if not self._exhausted:
            raise MethodFailure(ExaminationFailureCode.EXECUTION_FAILED)
        if not matches_recorded_integrity(
            self._digest.finish(),
            byte_size=self._recorded.byte_size,
            sha256=self._recorded.sha256,
            sha512=self._recorded.sha512,
        ):
            raise MethodFailure(ExaminationFailureCode.INTEGRITY_MISMATCH)


def run_method(
    definition: MethodDefinition, parameters: Mapping[str, Any], reader: EvidenceReader
) -> tuple[str, ...]:
    """Execute ``definition`` and return its checked Observation statements, or raise.

    Parameters were validated when the run was created; they are validated again here so a
    damaged row can never reach a Method. Output is checked against the Method's own contract
    (count, bounded size, measurements only) before anything can be published.
    """
    try:
        validated = definition.parameters_model.model_validate(
            validate_parameters(definition, dict(parameters)), strict=True
        )
    except ParameterValidationError:
        raise MethodFailure(ExaminationFailureCode.EXECUTION_FAILED) from None
    statements = definition.execute(reader, validated)
    reader.verify_integrity()
    assert_neutral_statements(definition, statements)
    return tuple(statements)
