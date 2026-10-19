from backend.common.store import ConflictError, add_event, read, save


def handler(event, context):
    incident = read(event["incident_id"])
    if incident and incident["status"] in {"ANALYZING", "SIMULATING"}:
        previous_revision = incident["revision"]
        incident["revision"] += 1
        incident["status"] = "FAILED"
        add_event(
            incident,
            "Workflow failed safely",
            "Inspect execution logs. No real recovery action was attempted.",
        )
        try:
            save(incident, previous_revision)
        except ConflictError:
            pass
    return {"incident_id": event["incident_id"], "status": "FAILED"}
