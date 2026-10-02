"""Bounded, incremental integrity hashing for V2.1 evidence objects."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import BinaryIO

HASH_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class DigestSummary:
    byte_size: int
    sha256: str
    sha512: str


class StreamingDigest:
    """Accumulate byte count and SHA-256/SHA-512 without retaining file content."""

    def __init__(self) -> None:
        self._sha256 = hashlib.sha256()
        self._sha512 = hashlib.sha512()
        self._byte_size = 0
        self._finished = False

    @property
    def byte_size(self) -> int:
        return self._byte_size

    def update(self, chunk: bytes) -> None:
        if self._finished:
            raise RuntimeError("digest accumulator is already finalized")
        if not isinstance(chunk, bytes):
            raise TypeError("digest chunks must be bytes")
        if not chunk:
            return
        self._sha256.update(chunk)
        self._sha512.update(chunk)
        self._byte_size += len(chunk)

    def finish(self) -> DigestSummary:
        if self._finished:
            raise RuntimeError("digest accumulator is already finalized")
        self._finished = True
        return DigestSummary(
            byte_size=self._byte_size,
            sha256=self._sha256.hexdigest(),
            sha512=self._sha512.hexdigest(),
        )


def digest_chunks(chunks: Iterable[bytes]) -> DigestSummary:
    """Hash an iterable of chunks through the one canonical update mechanism."""
    accumulator = StreamingDigest()
    for chunk in chunks:
        accumulator.update(chunk)
    return accumulator.finish()


def digest_file(stream: BinaryIO) -> DigestSummary:
    """Recheck a quarantined/preserved file using bounded reads, never read-all."""

    def chunks() -> Iterable[bytes]:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            yield chunk

    return digest_chunks(chunks())


def matches_recorded_integrity(
    computed: DigestSummary, *, byte_size: int, sha256: str, sha512: str
) -> bool:
    """The one comparison of recomputed bytes with the immutable intake values.

    Shared by independent verification (V2.2) and the examination reader (V2.3) so that
    "these are the recorded bytes" has exactly one definition.
    """
    return (computed.byte_size, computed.sha256, computed.sha512) == (byte_size, sha256, sha512)
