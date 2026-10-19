import logging

from backend.agent.narrative import explain
from backend.common.safety import canonical_hash, decision_digest, safety_gate
from backend.common.store import ConflictError, add_event, read, save

logger = logging.getLogger(__name__)


def handler(event, context):
    incident = read(event["incident_id"])
    if not incident or incident["status"] != "SIMULATING":
        return event
    previous_revision = incident["revision"]
    approval = incident.get("approval", {})
    persisted_digest = decision_digest({**incident, "revision": previous_revision - 1})
    valid = (
        approval.get("decision_digest") == incident.get("approval_digest") == persisted_digest
        and approval.get("decision_hash") == canonical_hash(incident.get("decision", {}))
        and approval.get("revision") == previous_revision - 1
        and approval.get("identity") == incident["owner"]
    )
    validation = safety_gate(incident["observations"], incident["decision"])
    if not valid or not validation["allowed"]:
        incident["status"] = "ESCALATED"
        add_event(
            incident,
            "Recovery blocked",
            "Persisted approval or current safety prerequisites are invalid.",
        )
    else:
        choice = incident["decision"]["choice"]
        incident["recovery"] = {
            "source": "simulation",
            "playbook": choice,
            "virtual_health": "investigated" if choice == "investigate_network" else "healthy",
            "aws_resources_changed": False,
        }
        add_event(
            incident,
            "Virtual playbook executed",
            f"Simulated {choice}. No AWS mutation API was called.",
        )
        incident["status"] = "COMPLETED"
        add_event(
            incident,
            "Investigation completed" if choice == "investigate_network" else "Recovery verified",
            "Virtual network change inspected; no rollback performed."
            if choice == "investigate_network"
            else "Simulated resource health checks passed.",
        )
    try:
        incident["report"], incident["report_latency_ms"] = explain(incident, report=True)
        incident["report_source"] = "strands-bedrock"
    except Exception:
        logger.exception("Report model unavailable for incident %s", incident["id"])
        incident["report"] = (
            f"# CloudRescue AI incident report\n\nIncident: {incident['id']}\nStatus: {incident['status']}\nSource: simulated recovery\n\n"
            + "\n".join(
                f"- {item['at']}: {item['title']} — {item['detail']}" for item in incident["events"]
            )
            + "\n\nAI narrative unavailable. This report contains recorded workflow events only."
        )
        incident["report_source"] = "recorded-events"
    incident["revision"] += 1
    try:
        save(incident, previous_revision)
    except ConflictError:
        logger.info("Another execution completed incident %s", incident["id"])
    return event
