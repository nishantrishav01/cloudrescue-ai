import math
from datetime import datetime, timedelta, timezone

import pytest

from backend.common.safety import (
    PLAYBOOKS,
    canonical_hash,
    decision_digest,
    safety_gate,
    validate_decision,
)
from backend.common.scenarios import evidence_for


def decision(choice="database_failover", confidence=0.95):
    return {
        "choice": choice,
        "confidence": confidence,
        "probabilities": {key: 0.96 if key == choice else 0.01 for key in PLAYBOOKS},
    }


@pytest.mark.parametrize(
    ("scenario", "choice"),
    [
        ("db-outage", "database_failover"),
        ("compute-overload", "scale_capacity"),
        ("network-failure", "investigate_network"),
    ],
)
def test_verified_scenario_can_be_recommended(scenario, choice):
    assert safety_gate(evidence_for(scenario), decision(choice))["allowed"]


@pytest.mark.parametrize(
    ("scenario", "choice"),
    [
        ("missing-standby", "database_failover"),
        ("conflicting-symptoms", "database_failover"),
        ("db-outage", "scale_capacity"),
        ("compute-overload", "replace_unhealthy_compute"),
    ],
)
def test_unsafe_or_mismatched_recommendation_is_blocked(scenario, choice):
    assert not safety_gate(evidence_for(scenario), decision(choice))["allowed"]


@pytest.mark.parametrize("confidence", [0.0, 0.59, 0.79])
def test_low_confidence_never_reaches_approval(confidence):
    assert not safety_gate(evidence_for("db-outage"), decision(confidence=confidence))["allowed"]


def test_stale_and_future_evidence_blocked():
    for offset in [-901, 60]:
        observations = evidence_for("db-outage")
        observations["observed_at"] = (
            datetime.now(timezone.utc) + timedelta(seconds=offset)
        ).isoformat()
        assert not safety_gate(observations, decision())["allowed"]


def test_replication_lag_required():
    observations = evidence_for("db-outage")
    observations.pop("replication_lag_seconds")
    assert not safety_gate(observations, decision())["allowed"]


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -0.1, 1.1])
def test_invalid_model_confidence_rejected(invalid):
    with pytest.raises(ValueError):
        validate_decision(decision(confidence=invalid))


def test_unknown_option_or_inconsistent_distribution_rejected():
    value = decision()
    value["probabilities"]["scale_capacity"] = 0.99
    with pytest.raises(ValueError):
        validate_decision(value)
    with pytest.raises(ValueError):
        validate_decision(decision("delete_database"))


def test_digest_binds_evidence_revision_choice_and_validation():
    incident = {
        "id": "sample",
        "revision": 2,
        "observations": evidence_for("db-outage"),
        "decision": decision(),
        "validation": {"allowed": True},
    }
    original = decision_digest(incident)
    incident["revision"] = 3
    assert decision_digest(incident) != original
    incident["revision"] = 2
    incident["observations"]["standby_db_health"] = "unavailable"
    assert decision_digest(incident) != original
    assert canonical_hash({"first": 1, "second": 2}) == canonical_hash({"second": 2, "first": 1})
