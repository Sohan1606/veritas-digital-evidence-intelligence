"""V2.3 Method concept: registry, contract, versioning and the binary characteristics Method."""

from __future__ import annotations

import ast
import decimal
import hashlib
import math
import random
import socket
import subprocess
import threading
from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import BaseModel

import app.examination as examination_package
from app.domain.enums import EvidenceType, ExaminationFailureCode
from app.examination.contracts import (
    FAILURE_MESSAGES,
    VERDICT_VOCABULARY,
    MethodFailure,
    ParameterValidationError,
    assert_neutral_statements,
    validate_parameters,
)
from app.examination.methods import binary_characteristics as binary
from app.examination.registry import METHOD_REGISTRY, MethodRegistry
from tests.examination_support import BINARY, DepthParameters, make_method

# The contract digest of core.binary_characteristics@1.0. If this changes, the Method's meaning
# changed: ship a NEW version instead of editing 1.0.
BINARY_1_0_DIGEST = "4e83253553424d15d2142ae504bfd695b85873bcf1d1ea2d8342b0512e94242e"


def statements_for(*chunks: bytes) -> tuple[str, ...]:
    return binary.statements(binary.characterize(binary.histogram(iter(chunks))))


def shake(total: int, seed: str = "v23") -> bytes:
    return hashlib.shake_256(seed.encode()).digest(total)


# --- Registry and versioning ---------------------------------------------------------------


def test_the_registry_holds_exactly_the_one_binary_characteristics_method() -> None:
    methods = METHOD_REGISTRY.all()
    assert [m.reference for m in methods] == ["core.binary_characteristics@1.0"]
    assert METHOD_REGISTRY.get("core.binary_characteristics", "1.0") is BINARY
    assert METHOD_REGISTRY.get("core.binary_characteristics", "1.1") is None
    assert METHOD_REGISTRY.get("core.binary_characteristics", "2.0") is None
    assert METHOD_REGISTRY.get("core.unknown", "1.0") is None


def test_a_method_version_is_registered_once_and_versions_coexist() -> None:
    with pytest.raises(ValueError, match="registered twice"):
        MethodRegistry([BINARY, BINARY])
    newer = replace(BINARY, version="1.1")
    registry = MethodRegistry([BINARY, newer])
    assert [m.version for m in registry.all()] == ["1.0", "1.1"]
    assert registry.get(BINARY.key, "1.0") is BINARY  # an older version is never replaced


def test_released_method_definition_is_pinned_by_digest() -> None:
    """Method version immutability: any change to the contract must change the version."""
    assert BINARY.digest() == BINARY_1_0_DIGEST
    changed = replace(BINARY, purpose=BINARY.purpose + " (edited)")
    assert changed.digest() != BINARY_1_0_DIGEST
    assert replace(BINARY, limitations=(*BINARY.limitations, "extra")).digest() != BINARY.digest()


def test_the_digest_does_not_depend_on_generated_json_schema_text() -> None:
    """A pydantic upgrade must not be able to change a pinned digest."""
    assert "parameters" in BINARY.describe()
    assert BINARY.parameter_signature() == [["extra", "forbid"]]


def test_method_definition_rejects_malformed_identity_and_missing_contract_parts() -> None:
    good = make_method()
    for bad in ("Core.Thing", "core", "core..x", "1core.x", "core.x" + "a" * 70):
        with pytest.raises(ValueError, match="key"):
            replace(good, key=bad)
    for bad in ("1", "1.0.0", "v1.0", "1.x", ""):
        with pytest.raises(ValueError, match="version"):
            replace(good, version=bad)
    with pytest.raises(ValueError, match="limitations"):
        replace(good, limitations=())
    with pytest.raises(ValueError, match="evidence type"):
        replace(good, supported_evidence_types=frozenset())
    with pytest.raises(ValueError, match="unique"):
        replace(good, outputs=(good.outputs[0], good.outputs[0]))


@pytest.mark.parametrize(
    "word", ["authentic", "fake", "manipulated", "suspicious", "malicious", "forged", "real"]
)
def test_method_metadata_cannot_state_conclusions(word: str) -> None:
    good = make_method()
    with pytest.raises(ValueError, match="measurements"):
        replace(good, name=f"The {word} examiner")
    with pytest.raises(ValueError, match="measurements"):
        replace(good, outputs=(replace(good.outputs[0], definition=f"Is the file {word}?"),))


def test_the_binary_method_metadata_is_exactly_what_the_specification_states() -> None:
    assert BINARY.key == "core.binary_characteristics"
    assert BINARY.version == "1.0"
    assert BINARY.name == "Binary Characteristics Examination"
    assert BINARY.purpose == (
        "Calculate deterministic byte-level characteristics of a preserved EvidenceObject "
        "without interpreting the bytes as instructions or making an authenticity judgment."
    )
    assert BINARY.supported_evidence_types == frozenset(EvidenceType)
    assert BINARY.deterministic is True and BINARY.enabled is True
    assert [o.key for o in BINARY.outputs] == [
        "byte_count",
        "shannon_entropy",
        "printable_ascii_ratio",
        "nul_ratio",
        "distinct_byte_values",
    ]
    # The limitations are authored disclaimers; they must name what is NOT determined.
    assert (
        "does not determine authenticity, origin, manipulation, malware status"
        in (BINARY.limitations[0])
    )
    described = BINARY.describe()
    assert described["parameters"]["additionalProperties"] is False
    assert described["parameters"]["properties"] == {}


# --- Parameter contract ----------------------------------------------------------------------


def test_the_binary_method_accepts_no_parameters_at_all() -> None:
    assert validate_parameters(BINARY, {}) == {}
    for bad in ({"depth": 1}, {"": 1}, {"chunk_bytes": 10}):
        with pytest.raises(ParameterValidationError) as raised:
            validate_parameters(BINARY, bad)
        assert raised.value.problems == ("extra_forbidden",)
    not_objects: list[object] = [None, [], "x", 3, True]
    for not_an_object in not_objects:
        with pytest.raises(ParameterValidationError):
            validate_parameters(BINARY, not_an_object)


def test_parameter_errors_name_rule_types_and_never_echo_input() -> None:
    probe = make_method(parameters_model=DepthParameters)
    secret_key, secret_value = "very-secret-key-name", "very-secret-value-9981"
    with pytest.raises(ParameterValidationError) as raised:
        validate_parameters(probe, {secret_key: secret_value, "depth": secret_value})
    rendered = f"{raised.value} {raised.value.problems}"
    assert secret_key not in rendered and secret_value not in rendered
    assert set(raised.value.problems) == {"extra_forbidden", "int_type"}


def test_typed_parameters_are_strict_bounded_and_defaulted() -> None:
    probe = make_method(parameters_model=DepthParameters)
    assert validate_parameters(probe, {}) == {"depth": 1, "label": "probe"}
    assert validate_parameters(probe, {"depth": 5}) == {"depth": 5, "label": "probe"}
    for bad in ({"depth": 0}, {"depth": 6}, {"depth": "3"}, {"depth": 2.0}, {"depth": True}):
        with pytest.raises(ParameterValidationError):
            validate_parameters(probe, bad)
    with pytest.raises(ParameterValidationError):
        validate_parameters(probe, {"label": "x" * 17})


# --- Fixed, documented measurements -----------------------------------------------------------


def test_empty_object_publishes_explicit_not_defined_statements() -> None:
    assert statements_for() == (
        "Observed byte count: 0.",
        "Observed Shannon byte entropy: not defined for an empty object.",
        "Observed printable ASCII byte ratio: not defined for an empty object.",
        "Observed NUL-byte ratio: not defined for an empty object.",
        "Observed distinct byte values: 0 of 256.",
    )
    assert statements_for(b"", b"") == statements_for()  # empty chunks add nothing


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            b"A",
            (
                "Observed byte count: 1.",
                "Observed Shannon byte entropy: 0.0000 bits per byte.",
                "Observed printable ASCII byte ratio: 1.0000.",
                "Observed NUL-byte ratio: 0.0000.",
                "Observed distinct byte values: 1 of 256.",
            ),
        ),
        (
            b"\x00" * 1000,
            (
                "Observed byte count: 1000.",
                "Observed Shannon byte entropy: 0.0000 bits per byte.",
                "Observed printable ASCII byte ratio: 0.0000.",
                "Observed NUL-byte ratio: 1.0000.",
                "Observed distinct byte values: 1 of 256.",
            ),
        ),
        (
            b"AB" * 500,
            (
                "Observed byte count: 1000.",
                "Observed Shannon byte entropy: 1.0000 bits per byte.",
                "Observed printable ASCII byte ratio: 1.0000.",
                "Observed NUL-byte ratio: 0.0000.",
                "Observed distinct byte values: 2 of 256.",
            ),
        ),
        (
            bytes(range(256)) * 4,  # uniform: 95 printable values (0x20-0x7E) of 256, one NUL
            (
                "Observed byte count: 1024.",
                "Observed Shannon byte entropy: 8.0000 bits per byte.",
                "Observed printable ASCII byte ratio: 0.3711.",
                "Observed NUL-byte ratio: 0.0039.",
                "Observed distinct byte values: 256 of 256.",
            ),
        ),
        (
            b"\t\n\r \x7e\x7f\x1f",  # TAB/LF/CR, 0x1F and DEL are not printable; space and ~ are
            (
                "Observed byte count: 7.",
                "Observed Shannon byte entropy: 2.8074 bits per byte.",
                "Observed printable ASCII byte ratio: 0.2857.",
                "Observed NUL-byte ratio: 0.0000.",
                "Observed distinct byte values: 7 of 256.",
            ),
        ),
    ],
)
def test_golden_statements_for_fixed_inputs(data: bytes, expected: tuple[str, ...]) -> None:
    assert statements_for(data) == expected


def _float_entropy(data: bytes) -> float:
    counts = [data.count(bytes([v])) for v in range(256)]
    total = len(data)
    return -math.fsum((c / total) * math.log2(c / total) for c in counts if c)


@pytest.mark.parametrize("seed", range(6))
def test_entropy_agrees_with_an_independent_floating_point_oracle(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - seeded, deterministic test data
    alphabet = rng.choice([2, 3, 17, 64, 200, 256])
    data = bytes(rng.randrange(alphabet) for _ in range(rng.randrange(1, 20_000)))
    published = float(binary.entropy_text(binary.histogram(iter([data])), len(data)))
    assert abs(published - _float_entropy(data)) <= 0.00005 + 1e-9


def test_ratios_round_half_even_exactly_like_an_independent_implementation() -> None:
    for count, total, expected in [
        (1, 20_000, "0.0000"),  # 0.00005 is a tie: rounds to the even neighbour
        (3, 20_000, "0.0002"),  # 0.00015 is a tie: rounds to the even neighbour
        (5, 20_000, "0.0002"),
        (1, 3, "0.3333"),
        (2, 3, "0.6667"),
        (5, 5, "1.0000"),
        (0, 7, "0.0000"),
    ]:
        assert binary.ratio_text(count, total) == expected
    rng = random.Random(7)  # noqa: S311 - seeded, deterministic test data
    for _ in range(3_000):
        total = rng.randrange(1, 10**9)
        count = rng.randrange(0, total + 1)
        exact = Decimal_quantize(Fraction(count, total))
        assert binary.ratio_text(count, total) == exact


def Decimal_quantize(value: Fraction) -> str:
    ctx = decimal.Context(prec=60, rounding=decimal.ROUND_HALF_EVEN)
    quantized = ctx.divide(decimal.Decimal(value.numerator), decimal.Decimal(value.denominator))
    return format(
        quantized.quantize(decimal.Decimal("0.0001"), rounding=decimal.ROUND_HALF_EVEN), "f"
    )


def test_results_do_not_depend_on_chunk_boundaries() -> None:
    data = shake(200_003)
    whole = statements_for(data)
    for size in (1, 7, 255, 4096, 65_536, 65_537, len(data)):
        pieces = [data[i : i + size] for i in range(0, len(data), size)]
        assert statements_for(*pieces) == whole


def test_results_are_deterministic_and_independent_of_the_global_decimal_context() -> None:
    data = shake(50_000, "context")
    baseline = statements_for(data)
    saved = decimal.getcontext().copy()
    try:
        decimal.getcontext().prec = 4
        decimal.getcontext().rounding = decimal.ROUND_DOWN
        assert statements_for(data) == baseline
    finally:
        decimal.setcontext(saved)
    results: list[tuple[str, ...]] = []

    def worker() -> None:
        decimal.getcontext().prec = 3
        results.append(statements_for(data))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [baseline] * 6


def test_the_published_wording_follows_the_specification_style() -> None:
    byte_count, entropy, printable, nul, distinct = statements_for(shake(123_456))
    assert byte_count == "Observed byte count: 123456."
    assert entropy.startswith("Observed Shannon byte entropy: ") and entropy.endswith(
        " bits per byte."
    )
    assert printable.startswith("Observed printable ASCII byte ratio: 0.")
    assert nul.startswith("Observed NUL-byte ratio: 0.")
    assert distinct == "Observed distinct byte values: 256 of 256."


# --- Neutral vocabulary and output contract ---------------------------------------------------


@pytest.mark.parametrize(
    "word",
    [
        "authentic",
        "fake",
        "manipulated",
        "suspicious",
        "malicious",
        "forged",
        "real",
        "Authenticity",
    ],
)
def test_the_output_contract_rejects_conclusions(word: str) -> None:
    probe = make_method()
    for text in (f"The file is {word}.", f"{word.upper()} content observed"):
        assert VERDICT_VOCABULARY.search(text)
        with pytest.raises(MethodFailure) as raised:
            assert_neutral_statements(probe, (text,))
        assert raised.value.code is ExaminationFailureCode.EXECUTION_FAILED


def test_the_output_contract_enforces_count_type_and_size() -> None:
    probe = make_method(outputs=2)
    assert_neutral_statements(probe, ("Observed byte count: 1.", "Observed byte count: 2."))
    for bad in [
        (),
        ("Observed byte count: 1.",),
        ("a", "b", "c"),
        ("  ", "Observed byte count: 2."),
        (3, "Observed byte count: 2."),
        ("x" * 501, "Observed byte count: 2."),
    ]:
        with pytest.raises(MethodFailure):
            assert_neutral_statements(probe, bad)


def test_the_real_method_never_emits_conclusion_vocabulary() -> None:
    for data in (b"", b"A", b"authentic fake real forged" * 50, shake(4096), bytes(range(256))):
        assert not any(VERDICT_VOCABULARY.search(s) for s in statements_for(data))
        assert_neutral_statements(BINARY, statements_for(data))


def test_every_failure_code_has_one_sanitized_fixed_message() -> None:
    assert set(FAILURE_MESSAGES) == set(ExaminationFailureCode)
    for message in FAILURE_MESSAGES.values():
        assert message.endswith(".") and "/" not in message and "\\" not in message
        assert "Traceback" not in message and "Error" not in message


# --- The Method cannot reach anything but its bytes -----------------------------------------


def test_the_method_runs_without_any_network_or_subprocess_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The binary Method keeps working with sockets and process creation made impossible."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the Method attempted a forbidden operation")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr("os.system", refuse)
    assert statements_for(shake(10_000))[0] == "Observed byte count: 10000."


_FORBIDDEN_IMPORTS = {
    "subprocess", "socket", "ssl", "http", "urllib", "requests", "httpx", "aiohttp", "ftplib",
    "smtplib", "telnetlib", "multiprocessing", "ctypes", "pathlib", "shutil", "tempfile", "glob",
    "pickle", "marshal", "importlib", "os", "sys",
}  # fmt: skip
_FORBIDDEN_BUILTINS = {"eval", "exec", "compile", "open", "__import__"}
_FORBIDDEN_ATTRIBUTES = {"system", "popen", "Popen", "execv", "execl", "spawnl", "spawnv", "fork"}


def _examination_sources() -> list[Path]:
    root = Path(examination_package.__file__).parent
    files = sorted(root.rglob("*.py"))
    assert len(files) >= 7
    return files


def test_the_examination_package_imports_no_process_network_or_filesystem_api() -> None:
    offenders: list[str] = []
    for path in _examination_sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [
                f"{path.name}: {name}" for name in names if name.split(".")[0] in _FORBIDDEN_IMPORTS
            ]
    assert offenders == []


def test_the_examination_package_never_evaluates_opens_or_spawns() -> None:
    offenders: list[str] = []
    for path in _examination_sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in _FORBIDDEN_BUILTINS:
                    offenders.append(f"{path.name}:{node.lineno} {func.id}")
                # ``re.compile`` is a pattern compiler, not the ``compile`` builtin.
                if isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_ATTRIBUTES:
                    offenders.append(f"{path.name}:{node.lineno} {func.attr}")
    assert offenders == []


def test_a_method_executor_receives_only_bytes_and_validated_parameters() -> None:
    """The executor signature is (EvidenceInput, parameters): no path, key, session or handle."""
    seen: list[object] = []

    def executor(source: object, parameters: BaseModel) -> tuple[str, ...]:
        seen.extend([source, parameters])
        return ("Observed byte count: 0.",)

    probe: Callable[..., object] = make_method(executor).execute
    assert probe(object(), BINARY.parameters_model()) == ("Observed byte count: 0.",)
    assert isinstance(seen[1], BaseModel)
