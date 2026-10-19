import logging
import time

from backend.agent.narrative import explain
from backend.agent.selector import select_playbook
from backend.common.safety import decision_digest, safety_gate
from backend.common.scenarios import evidence_for
from backend.common.store import ConflictError, add_event, read, save
from backend.live.discovery import evidence_for_resource

logger = logging.getLogger(__name__)


def handler(event, context):
    incident = read(event["incident_id"])
    if not incident or incident["status"] != "ANALYZING":
        return event
    previous_revision = incident["revision"]
    try:
        if incident.get("source") == "aws-live":
            incident["observations"], incident["telemetry"] = evidence_for_resource(
                incident["resource_arn"]
            )
        else:
            incident["observations"] = evidence_for(incident["scenario_id"])
        add_event(
            incident,
            "Evidence collected",
            "Read-only AWS resource and CloudWatch evidence collected with original metric timestamps."
            if incident.get("source") == "aws-live"
            else "Normalized virtual symptoms with source and observation timestamp.",
        )
        incident["decision"] = select_playbook(incident["observations"])
        add_event(
            incident,
            "Decider inference completed",
            f"Actual model selected {incident['decision']['choice']}. Choice is not permission to execute.",
        )
        incident["validation"] = safety_gate(incident["observations"], incident["decision"])
        incident["status"] = (
            "WAITING_APPROVAL" if incident["validation"]["allowed"] else "ESCALATED"
        )
        add_event(incident, "Safety checks completed", " ".join(incident["validation"]["reasons"]))
        try:
            incident["explanation"], incident["bedrock_latency_ms"] = explain(incident)
            incident["explanation_source"] = "strands-bedrock"
        except Exception:
            logger.exception("Narrative generation failed for incident %s", incident["id"])
            incident["explanation"] = (
                "The recorded Decider recommendation is available. The explanation model is unavailable; inspect the evidence and safety result."
            )
            incident["explanation_source"] = "system"
        incident["revision"] += 1
        incident["approval_expires_at"] = int(time.time()) + 600
        incident["approval_digest"] = decision_digest(incident)
    except Exception:
        logger.exception("Analysis failed closed for incident %s", incident["id"])
        incident["status"] = "ESCALATED"
        incident["revision"] += 1
        incident["validation"] = {
            "allowed": False,
            "reasons": [
                "Analysis unavailable. Inspect service logs and configure a verified inference container; operator investigation required."
            ],
        }
        incident["explanation"] = incident["validation"]["reasons"][0]
        add_event(incident, "Escalated safely", incident["explanation"])
    try:
        save(incident, previous_revision)
    except ConflictError:
        logger.info("Another execution updated incident %s", incident["id"])
    return event
