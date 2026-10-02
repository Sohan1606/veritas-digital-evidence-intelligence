"""core.binary_characteristics@1.0 - deterministic byte-level characteristics of one object.

The Method reads the PRESERVED bytes exactly once, in bounded chunks, and keeps only a 256-bin
histogram (memory is O(1) in the size of the evidence). Every published number is derived from
that histogram with the fixed definitions below, so the same byte stream always yields the
same statements on every platform:

* Byte count: the number of bytes read during this examination.
* Shannon byte entropy: H = -sum(p * log2(p)) over the byte values that occur, p = count / total,
  in bits per byte (0 to 8). Computed as (N*ln N - sum(c*ln c)) / (N*ln 2) with the ``decimal``
  module at 50 significant digits (its ``ln`` is correctly rounded), then rounded half-even to
  four decimal places. No floating point is involved.
* Printable ASCII byte ratio: bytes with a value from 0x20 to 0x7E inclusive, over the byte
  count. Tab, line feed and carriage return are not counted.
* NUL-byte ratio: bytes equal to 0x00 over the byte count.
  Ratios are exact integer arithmetic rounded half-even to four decimal places.
* Distinct byte values: how many of the 256 possible values occur at least once.

An empty object has no distribution: its byte count is 0 and distinct byte values is 0 of 256,
and the entropy and the two ratios are published as "not defined" rather than as an invented 0.

The bytes are only counted. They are never decoded, parsed, executed or compared with anything,
and nothing here is a statement about authenticity, origin, manipulation or content.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal

from pydantic import BaseModel, ConfigDict

from app.domain.enums import EvidenceType
from app.examination.contracts import (
    EvidenceInput,
    MethodDefinition,
    OutputSpec,
    ResourceLimits,
)
from app.services.evidence_hashing import HASH_CHUNK_BYTES

KEY = "core.binary_characteristics"
VERSION = "1.0"

PRINTABLE_FIRST = 0x20
PRINTABLE_LAST = 0x7E
BYTE_VALUES = 256
_PRECISION = 50
_FOUR_PLACES = Decimal("0.0001")
NOT_DEFINED = "not defined for an empty object"


class NoParameters(BaseModel):
    """This Method has no parameters; any supplied parameter is rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True, title="No parameters")


def histogram(chunks: Iterable[bytes]) -> list[int]:
    """Count byte values over bounded chunks without keeping any chunk."""
    counts = [0] * BYTE_VALUES
    for chunk in chunks:
        for value, occurrences in Counter(chunk).items():
            counts[value] += occurrences
    return counts


def ratio_text(count: int, total: int) -> str:
    """count / total as a four-place decimal string, by exact integer arithmetic (half-even)."""
    scaled, remainder = divmod(count * 10_000, total)
    twice = remainder * 2
    if twice > total or (twice == total and scaled % 2 == 1):
        scaled += 1
    return f"{scaled // 10_000}.{scaled % 10_000:04d}"


def entropy_text(counts: Sequence[int], total: int) -> str:
    """Shannon entropy in bits per byte, four places, from the histogram (see module doc)."""
    context = Context(prec=_PRECISION, rounding=ROUND_HALF_EVEN)
    n = Decimal(total)
    weighted = Decimal(0)
    for occurrences in counts:
        if occurrences:
            weighted = context.add(
                weighted, context.multiply(Decimal(occurrences), context.ln(Decimal(occurrences)))
            )
    numerator = context.subtract(context.multiply(n, context.ln(n)), weighted)
    bits = context.divide(numerator, context.multiply(n, context.ln(Decimal(2))))
    if bits < 0:  # only rounding noise below 1e-48 can be negative
        bits = Decimal(0)
    # ``Context.quantize``, not ``Decimal.quantize``: the latter would use the calling thread's
    # global context, and the result must not depend on it.
    return format(context.quantize(bits, _FOUR_PLACES), "f")


@dataclass(frozen=True, slots=True)
class Characteristics:
    byte_count: int
    entropy: str | None
    printable_ratio: str | None
    nul_ratio: str | None
    distinct_values: int


def characterize(counts: Sequence[int]) -> Characteristics:
    total = sum(counts)
    distinct = sum(1 for occurrences in counts if occurrences)
    if total == 0:
        return Characteristics(0, None, None, None, 0)
    printable = sum(counts[PRINTABLE_FIRST : PRINTABLE_LAST + 1])
    return Characteristics(
        byte_count=total,
        entropy=entropy_text(counts, total),
        printable_ratio=ratio_text(printable, total),
        nul_ratio=ratio_text(counts[0], total),
        distinct_values=distinct,
    )


def statements(result: Characteristics) -> tuple[str, ...]:
    """The five Observation statements, in the order declared by ``OUTPUTS``."""
    return (
        f"Observed byte count: {result.byte_count}.",
        "Observed Shannon byte entropy: "
        + (f"{result.entropy} bits per byte." if result.entropy else f"{NOT_DEFINED}."),
        "Observed printable ASCII byte ratio: "
        + (f"{result.printable_ratio}." if result.printable_ratio else f"{NOT_DEFINED}."),
        "Observed NUL-byte ratio: "
        + (f"{result.nul_ratio}." if result.nul_ratio else f"{NOT_DEFINED}."),
        f"Observed distinct byte values: {result.distinct_values} of {BYTE_VALUES}.",
    )


def execute(source: EvidenceInput, _parameters: BaseModel) -> tuple[str, ...]:
    return statements(characterize(histogram(source.chunks())))


OUTPUTS = (
    OutputSpec(
        key="byte_count",
        label="Byte count",
        definition="The number of bytes read from the preserved EvidenceObject during this run.",
    ),
    OutputSpec(
        key="shannon_entropy",
        label="Shannon byte entropy",
        definition=(
            "H = -sum(p * log2(p)) over the byte values that occur, with p = count / total, "
            "in bits per byte from 0 to 8. Rounded half-even to four decimal places."
        ),
    ),
    OutputSpec(
        key="printable_ascii_ratio",
        label="Printable ASCII byte ratio",
        definition=(
            "The fraction of bytes with a value from 0x20 to 0x7E inclusive. Tab, line feed and "
            "carriage return are not counted. Rounded half-even to four decimal places."
        ),
    ),
    OutputSpec(
        key="nul_ratio",
        label="NUL-byte ratio",
        definition=(
            "The fraction of bytes equal to 0x00. Rounded half-even to four decimal places."
        ),
    ),
    OutputSpec(
        key="distinct_byte_values",
        label="Distinct byte values",
        definition="How many of the 256 possible byte values occur at least once.",
    ),
)

BINARY_CHARACTERISTICS = MethodDefinition(
    key=KEY,
    version=VERSION,
    name="Binary Characteristics Examination",
    purpose=(
        "Calculate deterministic byte-level characteristics of a preserved EvidenceObject "
        "without interpreting the bytes as instructions or making an authenticity judgment."
    ),
    supported_evidence_types=frozenset(EvidenceType),
    input_requirements=(
        "The PRESERVED bytes of one EvidenceObject, read through private evidence storage.",
        "The bytes must still equal the byte count, SHA-256 and SHA-512 recorded at intake.",
    ),
    parameters_model=NoParameters,
    outputs=OUTPUTS,
    limitations=(
        "Measures byte-level statistics only. It does not determine authenticity, origin, "
        "manipulation, malware status, truthfulness or legal admissibility.",
        "Byte statistics depend on how a file is stored and compressed; they say nothing about "
        "what the content shows or means.",
        "The bytes are counted, never decoded, parsed or executed, and are not compared with "
        "other evidence.",
        "An object that no longer equals its recorded intake values fails the run instead of "
        "being examined.",
    ),
    limits=ResourceLimits(
        max_object_bytes=1_073_741_824,
        chunk_bytes=HASH_CHUNK_BYTES,
        max_runtime_seconds=900,
        max_observations=16,
        max_statement_chars=500,
    ),
    execute=execute,
)
