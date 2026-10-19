from copy import deepcopy
from datetime import datetime, timezone

SCENARIOS = {
    "db-outage": {
        "name": "Database outage",
        "description": "Primary database unavailable. A healthy standby is ready.",
        "service": "Amazon RDS",
        "severity": "Critical",
        "evidence": {
            "primary_db_health": "unavailable",
            "standby_db_health": "healthy",
            "replication_lag_seconds": 4,
            "connection_error_percent": 88,
            "cpu_percent": 34,
            "network_health": "healthy",
        },
    },
    "compute-overload": {
        "name": "Compute overload",
        "description": "A traffic surge is pushing compute capacity to its limit.",
        "service": "Amazon EC2",
        "severity": "High",
        "evidence": {
            "primary_db_health": "healthy",
            "cpu_percent": 98,
            "network_health": "healthy",
            "capacity_policy_valid": True,
            "simulated_resource": "virtual-web-cluster",
            "connection_error_percent": 2,
        },
    },
    "network-failure": {
        "name": "Network disruption",
        "description": "Investigate failed connectivity after a configuration change.",
        "service": "Amazon VPC",
        "severity": "High",
        "evidence": {
            "primary_db_health": "healthy",
            "cpu_percent": 22,
            "network_health": "degraded",
            "verified_change_id": "simulated-change-001",
            "connection_error_percent": 61,
        },
    },
    "missing-standby": {
        "name": "Missing evidence",
        "description": "Database unavailable, but standby health is unknown.",
        "service": "Amazon RDS",
        "severity": "Critical",
        "evidence": {
            "primary_db_health": "unavailable",
            "standby_db_health": "unknown",
            "connection_error_percent": 88,
            "cpu_percent": 30,
            "network_health": "healthy",
        },
    },
    "conflicting-symptoms": {
        "name": "Conflicting signals",
        "description": "Overlapping symptoms require an operator investigation.",
        "service": "Multiple services",
        "severity": "High",
        "evidence": {
            "primary_db_health": "unavailable",
            "cpu_percent": 99,
            "network_health": "degraded",
            "conflicting_evidence": True,
        },
    },
}


def list_scenarios() -> list[dict]:
    return [
        {"id": key, **{field: value for field, value in scenario.items() if field != "evidence"}}
        for key, scenario in SCENARIOS.items()
    ]


def evidence_for(scenario_id: str) -> dict:
    return {
        **deepcopy(SCENARIOS[scenario_id]["evidence"]),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "evidence_source": "simulation",
        "rpo_target_seconds": 300,
    }
