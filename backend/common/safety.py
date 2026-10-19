import hashlib
import json
import math
from datetime import datetime, timezone

PLAYBOOKS = {
    "database_failover": "Investigate virtual failover only when primary is unavailable, standby healthy, replication lag verified within RPO, and network healthy.",
    "replace_unhealthy_compute": "Replace a verified unhealthy virtual compute instance when the database and network are healthy.",
    "scale_capacity": "Scale the known virtual cluster when compute is saturated, database and network healthy, and capacity policy verified.",
    "investigate_network": "Investigate a verified simulated network change; do not roll back real infrastructure.",
    "escalate_to_human": "Escalate missing, stale, conflicting evidence or unmet prerequisites to an operator.",
}


def canonical_hash(value: dict) -> str:
    def normalize(item):
        if isinstance(item, dict):
            return {key: normalize(child) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize(child) for child in item]
        if isinstance(item, float) and item.is_integer():
            return int(item)
        return item

    return hashlib.sha256(
        json.dumps(normalize(value), sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def decision_digest(incident: dict) -> str:
    return canonical_hash(
        {
            "id": incident["id"],
            "revision": incident["revision"],
            "observations": incident["observations"],
            "decision": incident["decision"],
            "validation": incident["validation"],
        }
    )


def validate_decision(decision: dict) -> dict:
    choice = decision.get("choice")
    confidence = decision.get("confidence")
    probabilities = decision.get("probabilities", {})
    if (
        choice not in PLAYBOOKS
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        raise ValueError("Invalid Decider choice or confidence")
    if set(probabilities) != set(PLAYBOOKS):
        raise ValueError("Decider probabilities must cover the fixed playbook options")
    if any(
        not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1
        for value in probabilities.values()
    ):
        raise ValueError("Invalid Decider probability")
    if not math.isclose(sum(probabilities.values()), 1, abs_tol=0.02):
        raise ValueError("Decider probabilities do not sum to one")
    if probabilities[choice] < max(probabilities.values()):
        raise ValueError("Selected playbook is inconsistent with probabilities")
    return decision


def safety_gate(observations: dict, decision: dict, now: datetime | None = None) -> dict:
    reasons = []
    current = now or datetime.now(timezone.utc)
    try:
        observed = datetime.fromisoformat(observations["observed_at"])
        age = (current - observed).total_seconds()
        if age < -5 or age > 900:
            reasons.append("Evidence is stale or has an invalid timestamp.")
    except (KeyError, ValueError, TypeError):
        reasons.append("Evidence timestamp is missing or invalid.")
    if observations.get("evidence_source") != "simulation":
        reasons.append("This playbook requires an explicitly simulated incident state.")
    if observations.get("conflicting_evidence"):
        reasons.append("Conflicting symptoms require human investigation.")
    if decision.get("confidence", 0) < 0.8:
        reasons.append("Model confidence is below the uncalibrated 0.80 approval threshold.")
    choice = decision.get("choice")
    if choice == "database_failover":
        if (
            observations.get("primary_db_health") != "unavailable"
            or observations.get("standby_db_health") != "healthy"
        ):
            reasons.append("Primary outage and healthy standby must both be verified.")
        lag = observations.get("replication_lag_seconds")
        if not isinstance(lag, (float, int)) or not 0 <= lag <= observations.get(
            "rpo_target_seconds", 0
        ):
            reasons.append("Verified replication lag within the RPO target is required.")
        if observations.get("network_health") != "healthy":
            reasons.append("Network health must be verified before database failover.")
    elif choice in {"scale_capacity", "replace_unhealthy_compute"}:
        if observations.get("capacity_policy_valid") is not True or not observations.get(
            "simulated_resource"
        ):
            reasons.append("A verified capacity policy and virtual resource are required.")
        if (
            observations.get("primary_db_health") != "healthy"
            or observations.get("network_health") != "healthy"
        ):
            reasons.append("Healthy database and network must be verified.")
        if choice == "scale_capacity" and observations.get("cpu_percent", 0) < 85:
            reasons.append("Compute saturation is not supported by evidence.")
        if (
            choice == "replace_unhealthy_compute"
            and observations.get("compute_health") != "unhealthy"
        ):
            reasons.append("An unhealthy compute instance must be verified.")
    elif choice == "investigate_network":
        if observations.get("network_health") != "degraded" or not observations.get(
            "verified_change_id"
        ):
            reasons.append("A network failure and verified change are required for investigation.")
    else:
        reasons.append("The incident requires operator investigation.")
    return {
        "allowed": not reasons,
        "reasons": reasons
        or [
            "Evidence and playbook prerequisites verified. Operator approval required for simulation."
        ],
    }
