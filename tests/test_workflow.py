import copy

from backend.agent import handler as agent
from backend.agent.selector import InferenceUnavailable
from backend.common.safety import canonical_hash, decision_digest
from backend.simulation import handler as simulator
from tests.test_api import pending


def test_model_failure_escalates_without_fabricating_decision(monkeypatch):
    incident = {
        "id": "sample",
        "scenario_id": "db-outage",
        "status": "ANALYZING",
        "revision": 1,
        "events": [],
    }
    monkeypatch.setattr(agent, "read", lambda value: copy.deepcopy(incident))

    def unavailable(value):
        raise InferenceUnavailable("not configured")

    monkeypatch.setattr(agent, "select_playbook", unavailable)
    monkeypatch.setattr(agent, "save", lambda value, revision: incident.update(value))
    agent.handler({"incident_id": "sample"}, None)
    assert incident["status"] == "ESCALATED"
    assert "decision" not in incident


def test_simulator_rechecks_approval_and_never_calls_aws_mutations(monkeypatch):
    incident = pending()
    incident["status"] = "SIMULATING"
    incident["revision"] += 1
    incident["approval"] = {
        "identity": incident["owner"],
        "revision": 2,
        "decision_digest": incident["approval_digest"],
        "decision_hash": canonical_hash(incident["decision"]),
    }
    monkeypatch.setattr(simulator, "read", lambda value: copy.deepcopy(incident))
    monkeypatch.setattr(simulator, "save", lambda value, revision: incident.update(value))
    monkeypatch.setattr(
        simulator, "explain", lambda *args, **kwargs: ("Measured report fixture", 1)
    )
    simulator.handler({"incident_id": "incident-123"}, None)
    assert incident["status"] == "COMPLETED"
    assert incident["recovery"]["aws_resources_changed"] is False
    original = copy.deepcopy(incident)
    simulator.handler({"incident_id": "incident-123"}, None)
    assert incident == original


def test_tampered_persisted_decision_blocks_simulation(monkeypatch):
    incident = pending()
    incident["status"] = "SIMULATING"
    incident["revision"] = 3
    incident["approval"] = {
        "identity": incident["owner"],
        "revision": 2,
        "decision_digest": decision_digest({**incident, "revision": 2}),
        "decision_hash": "tampered",
    }
    monkeypatch.setattr(simulator, "read", lambda value: copy.deepcopy(incident))
    monkeypatch.setattr(simulator, "save", lambda value, revision: incident.update(value))
    monkeypatch.setattr(simulator, "explain", lambda *args, **kwargs: ("Blocked", 1))
    simulator.handler({"incident_id": "incident-123"}, None)
    assert incident["status"] == "ESCALATED"
    assert "recovery" not in incident
