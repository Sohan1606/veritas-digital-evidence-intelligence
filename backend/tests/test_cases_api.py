"""Read-only case API over the seeded demonstration dataset."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.schemas import DEMONSTRATION_NOTICE

BASE = "/api/v1/cases"


def test_list_cases_returns_demonstration_cases(client: TestClient) -> None:
    body = client.get(BASE).json()
    assert body["count"] == 2
    assert [c["id"] for c in body["items"]] == ["CASE-001", "CASE-002"]
    for case in body["items"]:
        assert case["demonstration"] is True
        assert case["notice"] == DEMONSTRATION_NOTICE


def test_case_detail(client: TestClient) -> None:
    body = client.get(f"{BASE}/CASE-001").json()
    assert body["state"] == "open"
    assert [o["id"] for o in body["objectives"]] == ["OBJ-001", "OBJ-002"]
    assert body["counts"] == {
        "evidence": 4,
        "analysis_runs": 0,
        "observations": 4,
        "findings": 2,
        "findings_awaiting_review": 2,
        "claims": 3,
        "assessments": 1,
        "relationships": 12,
    }
    assert "id" in body and "uuid" not in body


def test_unknown_case_is_404(client: TestClient) -> None:
    response = client.get(f"{BASE}/CASE-999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_evidence_list_marks_profile_availability(client: TestClient) -> None:
    items = client.get(f"{BASE}/CASE-001/evidence").json()["items"]
    assert {e["id"]: e["profile_recorded"] for e in items} == {
        "EVD-001": True,
        "EVD-002": True,
        "EVD-003": True,
        "EVD-004": False,
    }
    assert all(e["state"] == "registered" for e in items)


def test_evidence_profile_sections_are_honest(client: TestClient) -> None:
    body = client.get(f"{BASE}/CASE-001/evidence/EVD-001/profile").json()
    sections = [
        "identity",
        "integrity",
        "provenance",
        "quality",
        "acquisition_context",
        "classification",
    ]
    assert set(body["status_definitions"]) == {"verified", "partial", "unknown", "not_available"}
    # Nothing can be verified in V1: no content, no hashing, no examination.
    assert all(body[name]["status"] != "verified" for name in sections)
    assert body["integrity"]["status"] == "not_available"
    assert body["quality"]["status"] == "not_available"
    assert all(a["basis"] == "declared" for name in sections for a in body[name]["attributes"])
    assert "score" not in str(body).lower()


def test_evidence_profile_not_recorded_is_404(client: TestClient) -> None:
    response = client.get(f"{BASE}/CASE-001/evidence/EVD-004/profile")
    assert response.status_code == 404


def test_evidence_from_another_case_is_not_reachable(client: TestClient) -> None:
    assert client.get(f"{BASE}/CASE-002/evidence/EVD-001/profile").status_code == 404


def test_findings_expose_basis_limitations_and_alternatives(client: TestClient) -> None:
    items = client.get(f"{BASE}/CASE-001/findings").json()["items"]
    assert [f["id"] for f in items] == ["FND-001", "FND-002"]
    first = items[0]
    assert first["review_status"] == "unreviewed"
    assert {b["id"] for b in first["evidence_basis"]} == {"OBS-001", "OBS-002", "OBS-004"}
    assert all(
        b["origin"] == "manual" and b["analysis_run_id"] is None for b in first["evidence_basis"]
    )
    assert first["related_claims"][0] == {
        "id": "CLM-001",
        "statement": first["related_claims"][0]["statement"],
        "relationship": "contradicts",
        "rationale": first["related_claims"][0]["rationale"],
    }
    assert len(first["limitations"]) >= 1
    assert all(a["status"] == "open" for a in first["alternative_explanations"])
    excluded = [a for a in items[1]["alternative_explanations"] if a["status"] == "excluded"]
    assert excluded and excluded[0]["basis"]


def test_finding_detail_and_404(client: TestClient) -> None:
    assert client.get(f"{BASE}/CASE-001/findings/FND-002").json()["id"] == "FND-002"
    assert client.get(f"{BASE}/CASE-001/findings/FND-999").status_code == 404


def test_claims_with_links_and_assessments(client: TestClient) -> None:
    items = {c["id"]: c for c in client.get(f"{BASE}/CASE-001/claims").json()["items"]}
    assert set(items) == {"CLM-001", "CLM-002", "CLM-003"}
    assert items["CLM-002"]["related_findings"][0]["relationship"] == "supports"
    assert {e["id"] for e in items["CLM-002"]["referenced_evidence"]} == {"EVD-002", "EVD-003"}
    assessment = items["CLM-002"]["assessments"][0]
    assert assessment["state"] == "draft"
    assert assessment["assessed_by"].startswith("demo:")


def test_analysis_runs_are_empty_because_no_methods_exist(client: TestClient) -> None:
    assert client.get(f"{BASE}/CASE-001/analysis-runs").json() == {"items": [], "count": 0}


def test_case_knowledge_graph_is_consistent(client: TestClient) -> None:
    graph = client.get(f"{BASE}/CASE-001/graph").json()
    node_ids = {n["id"] for n in graph["nodes"]}
    assert {n["type"] for n in graph["nodes"]} == {
        "case",
        "evidence",
        "observation",
        "finding",
        "claim",
    }
    assert len(node_ids) == len(graph["nodes"]) == 1 + 4 + 4 + 2 + 3
    for edge in graph["relationships"]:
        assert edge["source"] in node_ids and edge["target"] in node_ids
    asserted = [e for e in graph["relationships"] if e["origin"] == "asserted"]
    structural = [e for e in graph["relationships"] if e["origin"] == "structural"]
    assert len(asserted) == 12
    assert all(e["id"].startswith("REL-") for e in asserted)
    assert {e["type"] for e in structural} == {"contains", "derived-from"}
    assert {e["type"] for e in asserted} == {
        "derived-from",
        "supports",
        "contradicts",
        "references",
    }


def test_audit_events_newest_first_with_limit(client: TestClient) -> None:
    body = client.get(f"{BASE}/CASE-001/audit-events", params={"limit": 5}).json()
    assert body["count"] == 5
    stamps = [e["occurred_at"] for e in body["items"]]
    assert stamps == sorted(stamps, reverse=True)
    assert client.get(f"{BASE}/CASE-001/audit-events", params={"limit": 0}).status_code == 422
    assert client.get(f"{BASE}/CASE-001/audit-events", params={"limit": 501}).status_code == 422


def test_empty_case_has_empty_collections(client: TestClient) -> None:
    for path in ("evidence", "findings", "claims", "analysis-runs"):
        assert client.get(f"{BASE}/CASE-002/{path}").json() == {"items": [], "count": 0}
    graph = client.get(f"{BASE}/CASE-002/graph").json()
    assert [n["id"] for n in graph["nodes"]] == ["CASE-002"]
    assert graph["relationships"] == []
