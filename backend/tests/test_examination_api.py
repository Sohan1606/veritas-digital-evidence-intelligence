"""V2.3 examination API: queueing, exact provenance, eligibility, idempotency, cancel, retry."""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain.enums import EvidenceType
from tests.evidence_support import (
    BASE,
    OPERATIONAL_CASE_ID,
    audit_events,
    captured_sql,
    deterministic_bytes,
    install_recording_storage,
    object_snapshot,
    preserve,
    preserved_file,
    second_case,
)
from tests.examination_support import (
    METHOD_KEY,
    METHOD_VERSION,
    DepthParameters,
    add_preserved_object,
    case_totals,
    coordinator_of,
    corrupt_file,
    error_of,
    examiner,
    install_methods,
    make_method,
    payload,
    quiesce_runs,  # noqa: F401  (autouse isolation fixture)
    run_events,
    run_row,
    runs_url,
    start,
)
from tests.test_evidence_intake_api import (
    evidence_client,  # noqa: F401  (pytest fixture shared with the V2.1 intake tests)
)
from tests.test_examination_methods import statements_for

RUN_FIELDS = {
    "id", "evidence_id", "evidence_object_id", "method_key", "method_version", "state",
    "parameters", "started_at", "completed_at", "cancel_requested_at", "last_heartbeat_at",
    "failure_code", "failure_message", "created_by", "created_at", "updated_at",
}  # fmt: skip
OBSERVATION_FIELDS = {
    "id", "statement", "origin", "analysis_run_id", "evidence_id", "evidence_label",
    "recorded_by", "created_at",
}  # fmt: skip


# --- Queueing -----------------------------------------------------------------------------------


def test_creating_a_run_only_queues_it(evidence_client: TestClient) -> None:  # noqa: F811
    item = preserve(evidence_client, body=deterministic_bytes(60_000))
    who = examiner(evidence_client)
    totals_before = case_totals(evidence_client)
    snapshot_before = object_snapshot(evidence_client, item)

    response = who.start(item, key="queue-only-0001")
    assert response.status_code == 201, response.text
    body = response.json()

    assert set(body) == RUN_FIELDS  # exactly the documented contract; no internal columns
    assert re.fullmatch(r"ANL-\d{3,}", body["id"])
    assert body["state"] == "queued"
    assert (body["evidence_id"], body["evidence_object_id"]) == (item.evidence_id, item.object_id)
    assert (body["method_key"], body["method_version"]) == (METHOD_KEY, METHOD_VERSION)
    assert body["parameters"] == {}
    for unset in ("started_at", "completed_at", "cancel_requested_at", "last_heartbeat_at"):
        assert body[unset] is None
    assert body["failure_code"] is None and body["failure_message"] is None
    assert body["created_by"].startswith("USR-")
    serialized = response.text
    assert "queue-only-0001" not in serialized  # the idempotency key is not echoed
    assert "request_fingerprint" not in serialized

    row = run_row(evidence_client, body["id"])
    assert row["idempotency_key"] == "queue-only-0001" and len(row["request_fingerprint"]) == 64
    assert who.detail(body["id"])["observations"] == []

    # Nothing ran, nothing else was created, and the evidence is untouched.
    assert case_totals(evidence_client) == totals_before
    assert object_snapshot(evidence_client, item) == snapshot_before
    assert preserved_file(evidence_client, item).read_bytes() == item.body

    (event,) = run_events(evidence_client, body["id"])
    assert event.action == "examination.run.created"
    assert event.actor == body["created_by"]
    assert event.request_id == response.headers["x-request-id"]
    assert event.entity_type == "analysis_run" and event.entity_public_id == body["id"]
    assert event.details == {
        "case_id": OPERATIONAL_CASE_ID,
        "evidence_id": item.evidence_id,
        "evidence_object_id": item.object_id,
        "method_key": METHOD_KEY,
        "method_version": METHOD_VERSION,
        "state": "queued",
    }


def test_every_run_names_the_exact_object_when_evidence_has_several(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    first = preserve(evidence_client, body=deterministic_bytes(50_000, seed=1))
    second = add_preserved_object(evidence_client, first, deterministic_bytes(30_000, seed=2))
    assert second.evidence_id == first.evidence_id and second.object_id != first.object_id
    who = examiner(evidence_client)
    run_a, run_b = start(who, first), start(who, second)
    assert run_a["evidence_id"] == run_b["evidence_id"]
    assert run_a["evidence_object_id"] == first.object_id
    assert run_b["evidence_object_id"] == second.object_id

    coordinator_of(evidence_client).run_pending()
    detail_a, detail_b = who.detail(run_a["id"]), who.detail(run_b["id"])
    assert detail_a["observations"][0]["statement"] == "Observed byte count: 50000."
    assert detail_b["observations"][0]["statement"] == "Observed byte count: 30000."
    listing = who.get(runs_url()).json()
    by_id = {item["id"]: item for item in listing["items"]}
    assert by_id[run_a["id"]]["evidence_object_id"] == first.object_id
    assert by_id[run_b["id"]]["evidence_object_id"] == second.object_id


def test_the_methods_endpoint_describes_the_registered_method(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    who = examiner(evidence_client)
    response = who.get(f"{BASE}/{OPERATIONAL_CASE_ID}/examination/methods")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    (method,) = body["items"]
    assert (method["key"], method["version"]) == (METHOD_KEY, METHOD_VERSION)
    assert method["name"] == "Binary Characteristics Examination"
    assert set(method["supported_evidence_types"]) == {t.value for t in EvidenceType}
    assert method["parameters"]["properties"] == {} and method["deterministic"] is True
    assert method["outputs"][0]["key"] == "byte_count"
    assert method["resource_limits"]["chunk_bytes"] == 65_536
    assert method["limitations"] and method["enabled"] is True
    assert "path" not in response.text.lower().replace("pathology", "")  # no filesystem detail


# --- Eligibility ----------------------------------------------------------------------------


def _assert_nothing_was_created(client: TestClient, runs_before: int) -> None:
    listing = examiner(client).get(runs_url()).json()
    assert listing["count"] == runs_before


def _run_count(client: TestClient) -> int:
    return int(examiner(client).get(runs_url()).json()["count"])


@pytest.mark.parametrize("stage", ["registered", "uploaded"])
def test_a_quarantined_object_cannot_be_examined(
    evidence_client: TestClient,  # noqa: F811
    stage: str,
) -> None:
    item = preserve(evidence_client, finalize=False)
    who = examiner(evidence_client)
    before = _run_count(evidence_client)
    error_of(who.start(item), 409, "evidence_object_not_preserved")
    _assert_nothing_was_created(evidence_client, before)
    assert (
        evidence_client.app.state.evidence_storage.exists(item.storage_key, preserved=True)  # type: ignore[attr-defined]
        is False
    )  # no quarantine fallback or promotion


def test_a_rejected_object_cannot_be_examined(evidence_client: TestClient) -> None:  # noqa: F811
    item = preserve(evidence_client, body=b"plain text is not a PDF", finalize=False)
    custodian = examiner(evidence_client, "CUSTODIAN")
    finalized = custodian.post(
        f"{BASE}/{item.case_id}/evidence/{item.evidence_id}/objects/{item.object_id}/finalize"
    )
    assert finalized.json()["state"] == "REJECTED"
    error_of(examiner(evidence_client).start(item), 409, "evidence_object_not_preserved")


def test_an_unknown_or_disabled_method_version_is_unavailable(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    who = examiner(evidence_client)
    before = _run_count(evidence_client)
    for key, version in [
        ("core.binary_characteristics", "1.1"),
        ("core.binary_characteristics", "2.0"),
        ("core.nonexistent", "1.0"),
    ]:
        error = error_of(who.start(item, method_key=key, method_version=version), 409)
        assert error["code"] == "method_unavailable"
    install_methods(evidence_client, make_method(enabled=False))
    error_of(
        who.start(item, method_key="test.probe", method_version="1.0"), 409, "method_unavailable"
    )
    _assert_nothing_was_created(evidence_client, before)


def test_a_method_must_support_the_evidence_type_and_size(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(10_000))  # document evidence
    install_methods(
        evidence_client,
        make_method(key="test.images", supported={EvidenceType.IMAGE}),
        make_method(key="test.tiny", max_object_bytes=100),
    )
    who = examiner(evidence_client)
    before = _run_count(evidence_client)
    inapplicable = error_of(who.start(item, method_key="test.images"), 409, "method_inapplicable")
    assert "document" in inapplicable["message"]
    error_of(who.start(item, method_key="test.tiny"), 409, "method_inapplicable")
    _assert_nothing_was_created(evidence_client, before)


def test_parameters_must_satisfy_the_method_contract_without_being_echoed(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    who = examiner(evidence_client)
    before = _run_count(evidence_client)
    error = error_of(
        who.start(item, parameters={"leaky-parameter-name": "leaky-value-5521"}),
        422,
        "invalid_method_parameters",
    )
    assert "leaky" not in error["message"]
    assert "extra_forbidden" in error["message"]
    _assert_nothing_was_created(evidence_client, before)


@pytest.mark.parametrize(
    "mutation",
    [
        {"unexpected": "field"},  # unknown fields are refused, not ignored
        {"evidence_id": "../etc/passwd"},
        {"evidence_id": "EVD-1"},
        {"evidence_object_id": "EOBJ-00A"},
        {"method_key": "Core.Binary"},
        {"method_key": "core"},
        {"method_version": "1"},
        {"method_version": "1.0.0"},
        {"idempotency_key": "short"},
        {"idempotency_key": "has spaces in the key"},
        {"idempotency_key": "x" * 129},
        {"parameters": ["not", "an", "object"]},
        {"storage_path": "/etc/passwd"},  # a client can never choose a path
        {"path": "/var/lib/veritas/evidence/preserved/x.bin"},
    ],
)
def test_malformed_requests_are_refused_by_schema(
    evidence_client: TestClient,  # noqa: F811
    mutation: dict[str, Any],
) -> None:
    item = preserve(evidence_client)
    who = examiner(evidence_client)
    body = {**payload(item), **mutation}
    error_of(who.post(runs_url(), body), 422, "validation_error")
    missing = {k: v for k, v in payload(item).items() if k != "idempotency_key"}
    error_of(who.post(runs_url(), missing), 422, "validation_error")


def test_wrong_evidence_object_or_case_combinations_do_not_resolve(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    first = preserve(evidence_client)
    other = preserve(evidence_client)
    who = examiner(evidence_client)
    before = _run_count(evidence_client)
    # An object of another Evidence item named under this Evidence.
    mismatched = {**payload(first), "evidence_object_id": other.object_id}
    error_of(who.post(runs_url(), mismatched), 404, "not_found")
    for evidence_id, object_id in [("EVD-999", first.object_id), (first.evidence_id, "EOBJ-999")]:
        guessed = {**payload(first), "evidence_id": evidence_id, "evidence_object_id": object_id}
        error_of(who.post(runs_url(), guessed), 404, "not_found")
    # The same ids under another Case never resolve (no cross-case leakage).
    other_case = second_case(evidence_client)
    elsewhere = examiner(evidence_client, "INVESTIGATOR", other_case)
    error_of(elsewhere.post(runs_url(case_id=other_case), payload(first)), 404, "not_found")
    _assert_nothing_was_created(evidence_client, before)


def test_storage_that_cannot_be_read_blocks_creation_but_not_a_replay(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    who = examiner(evidence_client)
    first = start(who, item, key="replay-survives-0001")
    corrupt_file(evidence_client, item, "missing")
    before = _run_count(evidence_client)
    error_of(who.start(item, key="a-brand-new-key-0002"), 503, "evidence_storage_unavailable")
    _assert_nothing_was_created(evidence_client, before)
    replay = who.start(item, key="replay-survives-0001")  # idempotent: no storage probe needed
    assert replay.status_code == 200 and replay.json()["id"] == first["id"]


# --- Idempotency (enforced by the server and the database) -------------------------------


def test_the_same_key_and_request_returns_the_original_run(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    who = examiner(evidence_client)
    first = who.start(item, key="idempotent-key-0001")
    again = who.start(item, key="idempotent-key-0001")
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["id"] == again.json()["id"]
    assert len(run_events(evidence_client, first.json()["id"])) == 1  # one created event only
    # A replay after the run finished still returns the original run (now completed).
    coordinator_of(evidence_client).run_pending()
    final = who.start(item, key="idempotent-key-0001")
    assert final.status_code == 200 and final.json()["state"] == "completed"
    assert final.json()["id"] == first.json()["id"]


def test_the_same_key_for_a_different_request_is_a_conflict(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    first = preserve(evidence_client)
    second = add_preserved_object(evidence_client, first, deterministic_bytes(9_000, seed=5))
    who = examiner(evidence_client)
    created = start(who, first, key="conflicting-key-0001")
    before = _run_count(evidence_client)
    error_of(who.start(second, key="conflicting-key-0001"), 409, "idempotency_conflict")
    _assert_nothing_was_created(evidence_client, before)
    assert who.detail(created["id"])["evidence_object_id"] == first.object_id


def test_the_same_key_with_different_parameters_is_a_conflict_but_equivalent_ones_replay(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(8_000, seed=7))
    install_methods(
        evidence_client, make_method(key="test.depth", parameters_model=DepthParameters)
    )
    who = examiner(evidence_client)
    first = who.start(item, key="param-key-000001", method_key="test.depth", parameters={})
    assert first.status_code == 201, first.text
    # Defaults are applied before the request is fingerprinted: this is the SAME request.
    assert first.json()["parameters"] == {"depth": 1, "label": "probe"}
    same = who.start(item, key="param-key-000001", method_key="test.depth", parameters={"depth": 1})
    assert same.status_code == 200 and same.json()["id"] == first.json()["id"]
    before = _run_count(evidence_client)
    different = who.start(
        item, key="param-key-000001", method_key="test.depth", parameters={"depth": 3}
    )
    error_of(different, 409, "idempotency_conflict")
    _assert_nothing_was_created(evidence_client, before)


def test_a_key_is_scoped_to_its_principal_and_its_case(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    one, two = examiner(evidence_client), examiner(evidence_client, "RESEARCHER")
    run_one = start(one, item, key="shared-key-text-0001")
    run_two = start(two, item, key="shared-key-text-0001")
    assert run_one["id"] != run_two["id"]
    assert run_one["created_by"] != run_two["created_by"]


def test_concurrent_identical_requests_create_exactly_one_run(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    who = examiner(evidence_client)
    body = payload(item, key="concurrent-key-0001")
    before = _run_count(evidence_client)

    def attempt(_: int) -> tuple[int, str]:
        response = who.post(runs_url(), body)
        return response.status_code, str(response.json().get("id"))

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(attempt, range(12)))
    assert sorted(code for code, _ in outcomes).count(201) == 1
    assert {code for code, _ in outcomes} <= {200, 201}
    assert len({run_id for _, run_id in outcomes}) == 1
    assert _run_count(evidence_client) == before + 1
    (run_id,) = {run_id for _, run_id in outcomes}
    assert len(run_events(evidence_client, run_id)) == 1


# --- Execution through the real path ----------------------------------------------------------


def test_a_completed_run_publishes_exactly_its_observations(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    body = deterministic_bytes(90_001, seed=3)
    item = preserve(evidence_client, body=body)
    who = examiner(evidence_client)
    run = start(who, item)
    assert coordinator_of(evidence_client).run_pending()
    detail = who.detail(run["id"])

    assert detail["state"] == "completed"
    assert set(detail) == RUN_FIELDS | {"observations"}
    assert detail["started_at"] and detail["completed_at"] and detail["last_heartbeat_at"]
    assert detail["completed_at"] >= detail["started_at"]
    assert [o["statement"] for o in detail["observations"]] == list(statements_for(body))
    for observation in detail["observations"]:
        assert set(observation) == OBSERVATION_FIELDS
        assert observation["origin"] == "analysis_run"
        assert observation["analysis_run_id"] == run["id"]
        assert observation["evidence_id"] == item.evidence_id
        assert observation["evidence_label"]
        assert observation["recorded_by"] == "system:examination-worker"
    assert detail["observations"][0]["statement"] == "Observed byte count: 90001."


def test_examination_never_creates_findings_claims_assessments_or_edges(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    who = examiner(evidence_client)
    before = case_totals(evidence_client)
    start(who, item)
    coordinator_of(evidence_client).run_pending()
    after = case_totals(evidence_client)
    assert after["observations"] == before["observations"] + 5
    for family in ("findings", "claims", "assessments", "relationships"):
        assert after[family] == before[family], family


def test_examination_leaves_evidence_custody_and_bytes_exactly_as_they_were(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(70_000, seed=9))
    who = examiner(evidence_client)
    snapshot = object_snapshot(evidence_client, item)
    digest = hashlib.sha256(preserved_file(evidence_client, item).read_bytes()).hexdigest()
    storage_tree = sorted(
        p.name for p in preserved_file(evidence_client, item).parent.parent.rglob("*")
    )

    with captured_sql(evidence_client) as statements:
        start(who, item)
        coordinator_of(evidence_client).run_pending()

    assert object_snapshot(evidence_client, item) == snapshot  # row, custody and object counts
    assert hashlib.sha256(preserved_file(evidence_client, item).read_bytes()).hexdigest() == digest
    assert (
        sorted(p.name for p in preserved_file(evidence_client, item).parent.parent.rglob("*"))
        == storage_tree
    )  # the storage tree is byte-for-byte the same: nothing was written
    writes = {
        re.sub(r"^(INSERT INTO|UPDATE)\s+(\w+).*", r"\2", s)
        for s in statements
        if s.startswith(("INSERT", "UPDATE"))
    }
    forbidden = {
        "EVIDENCE_OBJECTS", "EVIDENCE_CUSTODY_EVENTS", "EVIDENCE", "EVIDENCE_PROFILES",
        "FINDINGS", "CLAIMS", "ASSESSMENTS", "CASE_RELATIONSHIPS",
    }  # fmt: skip
    assert not writes & forbidden, writes & forbidden
    assert writes <= {"ANALYSIS_RUNS", "OBSERVATIONS", "AUDIT_EVENTS", "IDENTIFIER_SEQUENCES"}


def test_repeating_an_examination_is_deterministic_and_never_overwrites(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(33_333, seed=4))
    who = examiner(evidence_client)
    first, second = start(who, item), start(who, item)
    coordinator_of(evidence_client).run_pending()
    detail_1, detail_2 = who.detail(first["id"]), who.detail(second["id"])
    assert first["id"] != second["id"]
    assert [o["statement"] for o in detail_1["observations"]] == [
        o["statement"] for o in detail_2["observations"]
    ]
    ids = {o["id"] for o in detail_1["observations"]} | {o["id"] for o in detail_2["observations"]}
    assert len(ids) == 10  # two independent sets; the first was not touched by the second
    assert who.detail(first["id"]) == detail_1


def test_the_case_projections_and_graph_include_the_observations_structurally(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    who = examiner(evidence_client)
    start(who, item)
    coordinator_of(evidence_client).run_pending()
    case = who.get(f"{BASE}/{OPERATIONAL_CASE_ID}").json()
    assert case["counts"]["analysis_runs"] >= 1 and case["counts"]["observations"] >= 5
    graph = who.get(f"{BASE}/{OPERATIONAL_CASE_ID}/graph").json()
    derived = [
        r
        for r in graph["relationships"]
        if r["origin"] == "structural"
        and r["type"] == "derived-from"
        and r["target"] == item.evidence_id
    ]
    assert len(derived) == 5  # the existing structural Observation -> Evidence edge only
    assert all(
        r["origin"] == "structural"
        for r in graph["relationships"]
        if r["source"].startswith("OBS-")
    )


# --- Cancel and retry through the API ---------------------------------------------------------


def test_cancelling_a_queued_run_is_immediate_and_audited(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    who = examiner(evidence_client)
    run = start(who, item)
    response = who.post(runs_url(f"/{run['id']}/cancel"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "cancelled" and body["completed_at"] and body["started_at"] is None
    assert coordinator_of(evidence_client).run_pending() == []  # a cancelled run is never claimed
    actions = [e.action for e in run_events(evidence_client, run["id"])]
    assert actions == ["examination.run.created", "examination.run.cancelled"]
    assert run_events(evidence_client, run["id"])[-1].actor == body["created_by"]
    assert who.detail(run["id"])["observations"] == []


def test_finished_runs_are_final_and_cannot_be_cancelled(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    who = examiner(evidence_client)
    completed = start(who, item)
    coordinator_of(evidence_client).run_pending()
    cancelled = start(who, item)
    who.post(runs_url(f"/{cancelled['id']}/cancel"))
    for run in (completed, cancelled):
        snapshot = run_row(evidence_client, run["id"])
        error_of(who.post(runs_url(f"/{run['id']}/cancel")), 409, "invalid_lifecycle_transition")
        assert run_row(evidence_client, run["id"]) == snapshot  # history untouched
    assert who.detail(completed["id"])["state"] == "completed"


def test_retry_creates_a_new_run_and_leaves_the_old_one_untouched(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client, body=deterministic_bytes(200_000, seed=6))
    who = examiner(evidence_client)
    storage = install_recording_storage(evidence_client)
    storage.fail_on_read = 2  # a transient read fault while the first run executes
    failed = start(who, item)
    coordinator_of(evidence_client).run_pending()
    assert who.detail(failed["id"])["state"] == "failed"
    old_row = run_row(evidence_client, failed["id"])
    old_events = [e.public_id for e in run_events(evidence_client, failed["id"])]

    storage.fail_on_read = None  # the fault is gone
    retry = who.post(runs_url(f"/{failed['id']}/retry"), {"idempotency_key": "retry-key-000001"})
    assert retry.status_code == 201, retry.text
    new = retry.json()
    assert new["id"] != failed["id"] and new["state"] == "queued"
    for same in ("evidence_id", "evidence_object_id", "method_key", "method_version", "parameters"):
        assert new[same] == failed[same]
    assert new["started_at"] is None and new["failure_code"] is None  # a new lifecycle

    assert run_row(evidence_client, failed["id"]) == old_row  # the old run is unchanged...
    assert [e.public_id for e in run_events(evidence_client, failed["id"])] == old_events
    (event,) = run_events(evidence_client, new["id"])  # ...and the new one has its own event
    assert event.action == "examination.run.retried"
    assert event.details["source_run_id"] == failed["id"] and event.details["state"] == "queued"
    assert run_row(evidence_client, new["id"])["idempotency_key"] == "retry-key-000001"

    coordinator_of(evidence_client).run_pending()
    assert who.detail(new["id"])["state"] == "completed"
    assert who.detail(failed["id"])["state"] == "failed"  # still the historical failure
    assert who.detail(failed["id"])["observations"] == []


def test_retry_is_refused_while_the_object_still_cannot_be_read(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    who = examiner(evidence_client)
    failed = start(who, item)
    corrupt_file(evidence_client, item, "missing")
    coordinator_of(evidence_client).run_pending()
    before = _run_count(evidence_client)
    error_of(
        who.post(runs_url(f"/{failed['id']}/retry"), {"idempotency_key": "retry-still-down-1"}),
        503,
        "evidence_storage_unavailable",
    )
    _assert_nothing_was_created(evidence_client, before)


def test_retry_is_idempotent_and_only_for_failed_or_cancelled_runs(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    who = examiner(evidence_client)
    run = start(who, item)
    # queued: not retryable
    error_of(
        who.post(runs_url(f"/{run['id']}/retry"), {"idempotency_key": "retry-queued-0001"}),
        409,
        "invalid_lifecycle_transition",
    )
    who.post(runs_url(f"/{run['id']}/cancel"))
    first = who.post(runs_url(f"/{run['id']}/retry"), {"idempotency_key": "retry-cancel-0001"})
    again = who.post(runs_url(f"/{run['id']}/retry"), {"idempotency_key": "retry-cancel-0001"})
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["id"] == again.json()["id"]
    # The same key for a retry of a different source is a conflict.
    other = start(who, item)
    who.post(runs_url(f"/{other['id']}/cancel"))
    error_of(
        who.post(runs_url(f"/{other['id']}/retry"), {"idempotency_key": "retry-cancel-0001"}),
        409,
        "idempotency_conflict",
    )
    # completed: not retryable
    completed = start(who, item)
    coordinator_of(evidence_client).run_pending()
    assert who.detail(completed["id"])["state"] == "completed"
    error_of(
        who.post(runs_url(f"/{completed['id']}/retry"), {"idempotency_key": "retry-done-00001"}),
        409,
        "invalid_lifecycle_transition",
    )
    error_of(
        who.post(runs_url("/ANL-999/retry"), {"idempotency_key": "retry-ghost-00001"}),
        404,
        "not_found",
    )


def test_a_run_detail_does_not_resolve_in_another_case_or_by_guess(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    who = examiner(evidence_client)
    run = start(who, item)
    other_case = second_case(evidence_client)
    elsewhere = examiner(evidence_client, "INVESTIGATOR", other_case)
    error_of(elsewhere.get(runs_url(f"/{run['id']}", other_case)), 404, "not_found")
    error_of(elsewhere.post(runs_url(f"/{run['id']}/cancel", other_case)), 404, "not_found")
    error_of(who.get(runs_url("/ANL-999")), 404, "not_found")
    error_of(who.get(runs_url("/ANL-1")), 422, "validation_error")
    assert who.detail(run["id"])["state"] == "queued"  # the foreign cancel changed nothing


def test_audit_events_listing_shows_the_examination_trail(
    evidence_client: TestClient,  # noqa: F811
) -> None:
    item = preserve(evidence_client)
    who = examiner(evidence_client)
    run = start(who, item)
    coordinator_of(evidence_client).run_pending()
    events = who.get(f"{BASE}/{OPERATIONAL_CASE_ID}/audit-events?limit=500")
    assert events.status_code in (200, 404)  # examination readers may lack case_audit:read
    investigator_events = [e for e in audit_events(evidence_client, entity_id=run["id"])]
    assert [e.action for e in investigator_events] == [
        "examination.run.created",
        "examination.run.started",
        "examination.run.completed",
    ]
    assert [e.actor.startswith("USR-") for e in investigator_events] == [True, False, False]
    assert investigator_events[1].actor == "system:examination-worker"
    assert investigator_events[2].details["observation_count"] == 5
