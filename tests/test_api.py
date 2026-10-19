import copy
import time

import pytest
from fastapi.testclient import TestClient

from backend.api import main
from backend.common.safety import decision_digest, safety_gate
from backend.common.scenarios import evidence_for
from backend.common.store import ConflictError
from tests.test_safety import decision


@pytest.fixture
def client(monkeypatch):
    main.app.dependency_overrides[main.identity] = lambda: "operator-123"
    monkeypatch.setattr(
        main, "start_workflow", lambda *args, **kwargs: {"executionArn": "sample-execution"}
    )
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def pending():
    observations = evidence_for("db-outage")
    incident = {
        "id": "incident-123",
        "owner": "operator-123",
        "revision": 2,
        "status": "WAITING_APPROVAL",
        "observations": observations,
        "decision": decision(),
        "validation": safety_gate(observations, decision()),
        "approval_expires_at": time.time() + 600,
        "events": [],
    }
    incident["approval_digest"] = decision_digest(incident)
    return incident


def bind_storage(monkeypatch, incident):
    monkeypatch.setattr(main, "read", lambda incident_id: copy.deepcopy(incident))

    def save(value, expected_revision):
        if incident["revision"] != expected_revision:
            raise ConflictError("Concurrent update")
        incident.update(copy.deepcopy(value))

    monkeypatch.setattr(main, "save", save)


def test_private_api_requires_authentication():
    with TestClient(main.app) as unauthenticated:
        assert unauthenticated.get("/api/scenarios").status_code == 401
        assert unauthenticated.get("/health").status_code == 200


def test_create_returns_accepted_and_rejects_invalid_scenario(client, monkeypatch):
    monkeypatch.setattr(main, "save", lambda value: None)
    response = client.post("/api/incidents", json={"scenario_id": "db-outage"})
    assert response.status_code == 202
    assert response.json()["status"] == "ANALYZING"
    assert "owner" not in response.json()
    assert client.post("/api/incidents", json={"scenario_id": "destructive"}).status_code == 422


def test_approval_is_single_use_and_bound_to_revision(client, monkeypatch):
    incident = pending()
    bind_storage(monkeypatch, incident)
    payload = {"revision": 2, "decision_digest": incident["approval_digest"]}
    assert (
        client.post(
            "/api/incidents/incident-123/approve", json={**payload, "revision": 1}
        ).status_code
        == 409
    )
    response = client.post("/api/incidents/incident-123/approve", json=payload)
    assert response.status_code == 202
    assert response.json()["status"] == "SIMULATING"
    assert "approval" not in response.json()
    assert client.post("/api/incidents/incident-123/approve", json=payload).status_code == 409


def test_expired_or_tampered_approval_rejected(client, monkeypatch):
    incident = pending()
    bind_storage(monkeypatch, incident)
    payload = {"revision": 2, "decision_digest": incident["approval_digest"]}
    incident["approval_expires_at"] = time.time() - 1
    assert client.post("/api/incidents/incident-123/approve", json=payload).status_code == 409
    incident["approval_expires_at"] = time.time() + 600
    incident["observations"]["replication_lag_seconds"] = 9000
    assert client.post("/api/incidents/incident-123/approve", json=payload).status_code == 409


def test_other_operator_cannot_read_or_approve(client, monkeypatch):
    incident = pending()
    incident["owner"] = "someone-else"
    bind_storage(monkeypatch, incident)
    assert client.get("/api/incidents/incident-123").status_code == 404
    assert (
        client.post(
            "/api/incidents/incident-123/approve",
            json={"revision": 2, "decision_digest": incident["approval_digest"]},
        ).status_code
        == 404
    )


def test_benchmark_endpoint_returns_published_evaluation(client, monkeypatch):
    import io
    import json

    monkeypatch.setenv("BENCHMARK_BUCKET", "test-benchmark-bucket")
    monkeypatch.setattr(main, "list_for_owner", lambda owner: [])

    class FakeStorage:
        def get_object(self, **arguments):
            assert arguments == {"Bucket": "test-benchmark-bucket", "Key": "benchmarks/latest.json"}
            return {"Body": io.BytesIO(json.dumps({"case_count": 30, "accuracy": 0.8}).encode())}

    monkeypatch.setattr(main, "benchmark_storage", lambda: FakeStorage())
    response = client.get("/api/benchmarks")
    assert response.status_code == 200
    assert response.json()["evaluation"]["case_count"] == 30
    assert response.json()["count"] == 0


def test_live_and_restore_endpoints_require_authentication():
    with TestClient(main.app) as unauthenticated:
        for path in ["/api/live/resources", "/api/dr/configuration", "/api/dr/plans"]:
            assert unauthenticated.get(path).status_code == 401
        assert unauthenticated.post("/api/live/refresh", json={}).status_code == 401


def test_restore_approval_requires_dedicated_group_before_storage_access(client):
    response = client.post(
        "/api/dr/plans/dr-123/approve",
        json={
            "revision": 1,
            "approval_digest": "0" * 64,
            "confirmation": "RESTORE x TO y",
            "accept_aws_charges": True,
        },
    )
    assert response.status_code == 403


def test_live_investigation_cannot_use_simulation_approval(client, monkeypatch):
    incident = pending()
    incident["source"] = "aws-live"
    incident["observations"]["evidence_source"] = "aws-cloudwatch"
    incident["approval_digest"] = decision_digest(incident)
    bind_storage(monkeypatch, incident)
    assert (
        client.post(
            "/api/incidents/incident-123/approve",
            json={"revision": 2, "decision_digest": incident["approval_digest"]},
        ).status_code
        == 409
    )


def test_restore_scope_is_read_only_and_disabled_targets_fail_closed(client, monkeypatch):
    monkeypatch.setattr(
        main.recovery,
        "configuration",
        lambda: {
            "enabled": False,
            "policy": {"dr_region": "ap-southeast-1", "rto_seconds": 3600, "rpo_seconds": 900},
            "targets": [],
        },
    )
    response = client.get("/api/dr/configuration")
    assert response.status_code == 200
    assert response.json()["policy"]["dr_region"] == "ap-southeast-1"
    assert response.json()["can_approve"] is False
    assert client.post("/api/dr/configuration", json={"enabled": True}).status_code == 405
    assert client.post("/api/dr/plans", json={"target_id": "not-reviewed"}).status_code == 409


@pytest.mark.parametrize(
    "groups,expected",
    [
        (["RecoveryApprovers"], True),
        ('["RecoveryApprovers"]', True),
        ("RecoveryApprovers", True),
        (["Other"], False),
        ("OtherRecoveryApprovers", False),
    ],
)
def test_recovery_group_membership_is_exact(groups, expected):
    from types import SimpleNamespace

    request = SimpleNamespace(
        scope={
            "aws.event": {
                "requestContext": {"authorizer": {"jwt": {"claims": {"cognito:groups": groups}}}}
            }
        }
    )
    assert main.can_approve_restore(request) is expected
