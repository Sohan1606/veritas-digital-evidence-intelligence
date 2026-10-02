"""The controlled evidence reader and Method runner: bounded reads, integrity gate, limits."""

from __future__ import annotations

import contextlib
import hashlib
import io
from collections.abc import Callable
from dataclasses import replace
from typing import Any, BinaryIO, cast

import pytest
from pydantic import BaseModel

from app.domain.enums import ExaminationFailureCode
from app.examination.contracts import (
    EvidenceInput,
    ExecutionCancelled,
    MethodFailure,
    ResourceLimits,
)
from app.examination.runner import EvidenceReader, RecordedIntegrity, run_method
from app.services.evidence_hashing import HASH_CHUNK_BYTES, digest_chunks
from tests.examination_support import BINARY, DepthParameters, consume_all, make_method
from tests.test_examination_methods import shake


class SizedStream(io.RawIOBase):
    """A stream that records every read size and refuses unbounded reads."""

    def __init__(self, data: bytes, *, fail_after: int | None = None) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)
        self.sizes: list[int | None] = []
        self._fail_after = fail_after

    def readable(self) -> bool:
        return True

    def read(self, size: int | None = -1) -> bytes:
        self.sizes.append(size)
        if size is None or size < 0:
            raise AssertionError("unbounded read")
        if self._fail_after is not None and len(self.sizes) > self._fail_after:
            raise OSError(5, "synthetic failure at /private/secret/path")
        return self._inner.read(size)


def recorded_for(data: bytes) -> RecordedIntegrity:
    summary = digest_chunks([data])
    return RecordedIntegrity(summary.byte_size, summary.sha256, summary.sha512)


def reader_for(
    data: bytes,
    *,
    stream: SizedStream | None = None,
    recorded: RecordedIntegrity | None = None,
    limits: ResourceLimits | None = None,
    checkpoint: Callable[[], None] = lambda: None,
    monotonic: Callable[[], float] | None = None,
) -> EvidenceReader:
    extra: dict[str, Any] = {} if monotonic is None else {"monotonic": monotonic}
    return EvidenceReader(
        cast(BinaryIO, stream or SizedStream(data)),
        recorded=recorded or recorded_for(data),
        limits=limits or BINARY.limits,
        checkpoint=checkpoint,
        **extra,
    )


def failure_of(action: Callable[[], object]) -> ExaminationFailureCode:
    with pytest.raises(MethodFailure) as raised:
        action()
    return raised.value.code


# --- Bounded reading ---------------------------------------------------------------------------


def test_the_reader_yields_bounded_chunks_and_reads_the_whole_object_once() -> None:
    data = shake(3 * HASH_CHUNK_BYTES + 123)
    stream = SizedStream(data)
    chunks = list(reader_for(data, stream=stream).chunks())
    assert b"".join(chunks) == data
    assert [len(c) for c in chunks] == [HASH_CHUNK_BYTES] * 3 + [123]
    # Every read was bounded; the last read returned b"" at the end of the object.
    assert stream.sizes and all(size == HASH_CHUNK_BYTES for size in stream.sizes)
    assert len(stream.sizes) == len(chunks) + 1


def test_the_reader_is_single_use() -> None:
    reader = reader_for(b"abc")
    assert consume_all(reader) == 3
    with pytest.raises(RuntimeError, match="only once"):
        list(reader.chunks())


def test_the_reader_exposes_no_path_key_or_handle_to_a_method() -> None:
    reader = reader_for(b"abc")
    public = sorted(name for name in dir(reader) if not name.startswith("_"))
    assert public == ["chunks", "verify_integrity"]
    assert isinstance(reader, EvidenceReader)
    # Statically, a Method is typed against the narrow Protocol that only has ``chunks``.
    assert [n for n in vars(EvidenceInput) if not n.startswith("_")] == ["chunks"]


def test_the_checkpoint_runs_before_every_read_including_the_final_one() -> None:
    calls: list[int] = []
    data = shake(2 * HASH_CHUNK_BYTES)
    reader = reader_for(data, checkpoint=lambda: calls.append(1))
    assert consume_all(reader) == len(data)
    assert len(calls) == 3  # two chunks plus the read that discovers the end


def test_a_cancellation_raised_at_a_checkpoint_stops_the_read_immediately() -> None:
    stream = SizedStream(shake(5 * HASH_CHUNK_BYTES))
    calls = 0

    def checkpoint() -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ExecutionCancelled

    reader = reader_for(b"", stream=stream, recorded=recorded_for(b""), checkpoint=checkpoint)
    with pytest.raises(ExecutionCancelled):
        consume_all(reader)
    assert len(stream.sizes) == 2  # nothing was read after the cancellation was observed


# --- The integrity gate -----------------------------------------------------------------------


def test_an_object_that_equals_its_recorded_values_is_accepted() -> None:
    data = shake(70_000)
    statements = run_method(BINARY, {}, reader_for(data))
    assert statements[0] == "Observed byte count: 70000."


@pytest.mark.parametrize("how", ["modified", "truncated", "expanded", "replaced"])
def test_bytes_that_differ_from_the_recorded_intake_values_fail_closed(how: str) -> None:
    original = shake(70_000)
    changed = {
        "modified": bytes([original[0] ^ 1]) + original[1:],
        "truncated": original[:-1],
        "expanded": original + b"\x00",
        "replaced": shake(70_000, "another object of the same size"),
    }[how]
    code = failure_of(
        lambda: run_method(BINARY, {}, reader_for(changed, recorded=recorded_for(original)))
    )
    assert code is ExaminationFailureCode.INTEGRITY_MISMATCH


def test_a_zero_byte_object_is_examined_when_it_equals_its_record() -> None:
    statements = run_method(BINARY, {}, reader_for(b""))
    assert statements[0] == "Observed byte count: 0."
    assert "not defined" in statements[1]


def test_a_method_that_does_not_read_the_whole_object_cannot_publish() -> None:
    def partial(source: EvidenceInput, _parameters: BaseModel) -> tuple[str, ...]:
        next(iter(source.chunks()))
        return ("Observed byte count: 1.",)

    data = shake(3 * HASH_CHUNK_BYTES)
    code = failure_of(lambda: run_method(make_method(partial), {}, reader_for(data)))
    assert code is ExaminationFailureCode.EXECUTION_FAILED


def test_a_method_that_swallows_a_read_failure_still_cannot_publish() -> None:
    def swallow(source: EvidenceInput, _parameters: BaseModel) -> tuple[str, ...]:
        with contextlib.suppress(MethodFailure):
            consume_all(source)
        return ("Observed byte count: 5.",)

    data = shake(2 * HASH_CHUNK_BYTES)
    stream = SizedStream(data, fail_after=1)
    code = failure_of(lambda: run_method(make_method(swallow), {}, reader_for(data, stream=stream)))
    assert code is ExaminationFailureCode.EXECUTION_FAILED


# --- Failures and limits -----------------------------------------------------------------------


def test_a_read_failure_is_reported_as_evidence_unavailable_without_detail() -> None:
    data = shake(3 * HASH_CHUNK_BYTES)
    stream = SizedStream(data, fail_after=1)
    with pytest.raises(MethodFailure) as raised:
        run_method(BINARY, {}, reader_for(data, stream=stream))
    assert raised.value.code is ExaminationFailureCode.EVIDENCE_UNAVAILABLE
    assert "secret" not in str(raised.value) and "private" not in repr(raised.value)
    assert raised.value.__cause__ is None  # the OS error is not chained into the failure


def test_the_byte_limit_stops_a_runaway_read() -> None:
    limits = replace(BINARY.limits, max_object_bytes=HASH_CHUNK_BYTES)
    data = shake(2 * HASH_CHUNK_BYTES)
    code = failure_of(lambda: run_method(BINARY, {}, reader_for(data, limits=limits)))
    assert code is ExaminationFailureCode.RESOURCE_LIMIT_EXCEEDED


def test_the_runtime_limit_is_enforced_at_checkpoints() -> None:
    clock = iter([0.0, 0.0, 10.0, 20.0, 30.0])
    limits = replace(BINARY.limits, max_runtime_seconds=5)
    data = shake(4 * HASH_CHUNK_BYTES)
    code = failure_of(
        lambda: run_method(
            BINARY, {}, reader_for(data, limits=limits, monotonic=lambda: next(clock))
        )
    )
    assert code is ExaminationFailureCode.RESOURCE_LIMIT_EXCEEDED


def test_a_stream_that_returns_more_than_requested_is_refused() -> None:
    class Greedy(SizedStream):
        def read(self, size: int | None = -1) -> bytes:
            return b"x" * ((size or 0) + 1)

    code = failure_of(lambda: run_method(BINARY, {}, reader_for(b"", stream=Greedy(b""))))
    assert code is ExaminationFailureCode.EXECUTION_FAILED


# --- Parameters and output are re-checked at execution time ----------------------------------


def test_damaged_parameters_never_reach_a_method() -> None:
    called: list[bool] = []

    def spy(source: EvidenceInput, _parameters: BaseModel) -> tuple[str, ...]:
        called.append(True)
        return ("Observed byte count: 0.",)

    probe = make_method(spy, parameters_model=DepthParameters)
    code = failure_of(lambda: run_method(probe, {"depth": 99}, reader_for(b"")))
    assert code is ExaminationFailureCode.EXECUTION_FAILED and not called


def test_output_that_breaks_the_contract_is_never_returned() -> None:
    def wrong_count(source: EvidenceInput, _parameters: BaseModel) -> tuple[str, ...]:
        consume_all(source)
        return ("Observed byte count: 0.", "Observed byte count: 0.")

    def verdict(source: EvidenceInput, _parameters: BaseModel) -> tuple[str, ...]:
        consume_all(source)
        return ("This file is authentic.",)

    for executor in (wrong_count, verdict):
        code = failure_of(lambda e=executor: run_method(make_method(e), {}, reader_for(b"")))  # type: ignore[misc]
        assert code is ExaminationFailureCode.EXECUTION_FAILED


def test_the_reader_feeds_the_canonical_digest_not_a_second_hash_implementation() -> None:
    data = shake(1_000)
    reader = reader_for(data)
    consume_all(reader)
    reader.verify_integrity()  # equal to hashlib results through StreamingDigest
    assert recorded_for(data).sha256 == hashlib.sha256(data).hexdigest()
