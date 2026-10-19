import hmac
import json
import logging
import os
import re
import time
import uuid
from functools import lru_cache
from typing import Literal

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from mangum import Mangum
from pydantic import BaseModel, ConfigDict, Field

from backend.common.safety import canonical_hash, decision_digest, safety_gate
from backend.common.scenarios import SCENARIOS, list_scenarios
from backend.common.store import ConflictError, add_event, list_for_owner, read, save, utc_now
from backend.live.discovery import client as aws_client
from backend.live.discovery import evidence_for_resource, snapshot
from backend.recovery import service as recovery

logger = logging.getLogger(__name__)
app = FastAPI(title="CloudRescue AI", docs_url=None, redoc_url=None, openapi_url=None)


class CreateIncident(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(max_length=60)


class ApproveIncident(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    decision_digest: str = Field(min_length=64, max_length=64, pattern="^[a-f0-9]+$")


class LiveInvestigation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_arn: str = Field(min_length=10, max_length=2048)


class CreateRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(pattern="^[a-z][a-z0-9-]{1,50}$")


class ApproveRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    approval_digest: str = Field(pattern="^[a-f0-9]{64}$")
    confirmation: str = Field(max_length=150)
    accept_aws_charges: Literal[True]


@app.exception_handler(recovery.RecoveryBlocked)
async def blocked_restore(request: Request, issue: recovery.RecoveryBlocked):
    return JSONResponse(status_code=409, content={"detail": str(issue)})


def can_approve_restore(request: Request) -> bool:
    claims = (
        request.scope.get("aws.event", {})
        .get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    groups = claims.get("cognito:groups", [])
    if isinstance(groups, str):
        groups = re.split(r'[\s,\[\]"]+', groups)
    return "RecoveryApprovers" in groups


def identity(request: Request) -> str:
    claims = (
        request.scope.get("aws.event", {})
        .get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    subject = claims.get("sub")
    if not subject or claims.get("token_use") != "access":
        raise HTTPException(401, "Operator sign-in required.")
    return subject


def restore_approver(request: Request, owner: str = Depends(identity)) -> str:
    if not can_approve_restore(request):
        raise HTTPException(
            403, "Membership in RecoveryApprovers is required for real restoration."
        )
    return owner


@lru_cache
def workflows():
    return boto3.client(
        "stepfunctions",
        config=Config(
            connect_timeout=3,
            read_timeout=10,
            retries={"mode": "adaptive", "total_max_attempts": 3},
        ),
    )


def owned_incident(incident_id: str, owner: str) -> dict:
    if len(incident_id) > 64:
        raise HTTPException(404, "Incident not found.")
    incident = read(incident_id)
    if not incident or incident["owner"] != owner:
        raise HTTPException(404, "Incident not found.")
    return incident


def public_incident(incident: dict) -> dict:
    return {
        key: value for key, value in incident.items() if key not in {"owner", "ttl", "approval"}
    }


def start_workflow(incident: dict, recovery: bool = False):
    return workflows().start_execution(
        stateMachineArn=os.environ["WORKFLOW_ARN"],
        name=f"{'recovery' if recovery else 'analysis'}-{incident['id']}",
        input=json.dumps(
            {"incident_id": incident["id"], "phase": "recovery" if recovery else "analysis"},
            sort_keys=True,
        ),
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "cloudrescue-ai",
        "remediation": "explicitly-approved-restores-only",
    }


@app.get("/api/scenarios")
def scenarios(owner: str = Depends(identity)):
    return list_scenarios()


@app.post("/api/incidents", status_code=202)
def create_incident(body: CreateIncident, owner: str = Depends(identity)):
    if body.scenario_id not in SCENARIOS:
        raise HTTPException(422, "Unknown scenario.")
    incident = {
        "id": str(uuid.uuid4()),
        "owner": owner,
        "scenario_id": body.scenario_id,
        "status": "ANALYZING",
        "source": "simulation",
        "created_at": utc_now(),
        "revision": 1,
        "observations": {},
        "events": [],
        "ttl": int(time.time()) + 30 * 86400,
    }
    add_event(incident, "Incident detected", "Virtual incident queued for asynchronous analysis.")
    save(incident)
    try:
        execution = start_workflow(incident)
    except Exception as issue:
        logger.exception("Unable to start analysis for %s", incident["id"])
        incident["status"] = "FAILED"
        incident["revision"] += 1
        add_event(
            incident,
            "Workflow unavailable",
            "No recovery was attempted. Start another incident after resolving workflow access.",
        )
        save(incident, 1)
        raise HTTPException(
            503, "Analysis workflow unavailable. No recovery was attempted."
        ) from issue
    return {**public_incident(incident), "execution_id": execution["executionArn"]}


@app.get("/api/incidents")
def get_incidents(owner: str = Depends(identity)):
    return [public_incident(item) for item in list_for_owner(owner)]


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str, owner: str = Depends(identity)):
    return public_incident(owned_incident(incident_id, owner))


@app.post("/api/incidents/{incident_id}/approve", status_code=202)
def approve_incident(incident_id: str, body: ApproveIncident, owner: str = Depends(identity)):
    incident = owned_incident(incident_id, owner)
    if incident["status"] != "WAITING_APPROVAL" or incident["revision"] != body.revision:
        raise HTTPException(
            409, "This decision is no longer awaiting approval. Refresh the incident."
        )
    if time.time() > incident.get("approval_expires_at", 0):
        raise HTTPException(
            409, "Approval expired. Launch a new incident to gather fresh evidence."
        )
    digest = decision_digest(incident)
    if not hmac.compare_digest(body.decision_digest, digest) or digest != incident.get(
        "approval_digest"
    ):
        raise HTTPException(409, "Approval does not match the persisted decision and evidence.")
    validation = safety_gate(incident["observations"], incident["decision"])
    if not validation["allowed"]:
        raise HTTPException(409, "Current evidence no longer passes the safety gate.")
    incident["approval"] = {
        "identity": owner,
        "at": utc_now(),
        "revision": body.revision,
        "decision_digest": digest,
        "decision_hash": canonical_hash(incident["decision"]),
    }
    incident["status"] = "SIMULATING"
    incident["revision"] += 1
    add_event(
        incident,
        "Operator approved",
        "Authenticated, single-use approval for the exact virtual playbook revision.",
    )
    try:
        save(incident, body.revision)
    except ConflictError as issue:
        raise HTTPException(409, str(issue)) from issue
    try:
        start_workflow(incident, recovery=True)
    except Exception as issue:
        logger.exception("Recovery workflow start failed for %s", incident["id"])
        failed = read(incident["id"])
        if (
            failed
            and failed["status"] == "SIMULATING"
            and failed["revision"] == incident["revision"]
        ):
            failed["status"] = "FAILED"
            failed["revision"] += 1
            add_event(
                failed,
                "Recovery scheduling failed",
                "Workflow start could not be confirmed. Inspect execution history before retrying with a new incident.",
            )
            try:
                save(failed, incident["revision"])
            except ConflictError:
                pass
        raise HTTPException(
            503, "Recovery scheduling could not be confirmed. Check the incident history."
        ) from issue
    return public_incident(incident)


@app.get("/api/benchmarks")
def benchmarks(owner: str = Depends(identity)):
    decisions = [
        item["decision"]
        for item in list_for_owner(owner)
        if item.get("decision", {}).get("source") == "strands-decider"
    ]
    evaluation = None
    bucket = os.environ.get("BENCHMARK_BUCKET")
    if bucket:
        storage = benchmark_storage()
        try:
            with storage.get_object(Bucket=bucket, Key="benchmarks/latest.json")["Body"] as body:
                evaluation = json.loads(body.read())
        except storage.exceptions.NoSuchKey:
            pass
    return {
        "count": len(decisions),
        "average_latency_ms": sum(item["latency_ms"] for item in decisions) / len(decisions)
        if decisions
        else None,
        "source": "Actual persisted Decider calls in your latest 50 incidents; includes invocation overhead.",
        "evaluation": evaluation,
    }


@lru_cache
def benchmark_storage():
    return boto3.client(
        "s3",
        config=Config(
            connect_timeout=3,
            read_timeout=10,
            retries={"mode": "adaptive", "total_max_attempts": 3},
        ),
    )


@app.get("/api/live/resources")
def live_resources(owner: str = Depends(identity)):
    return snapshot() or {
        "source": "aws-live",
        "account_id": os.environ.get("AWS_ACCOUNT_ID"),
        "region": os.environ.get("AWS_REGION"),
        "collected_at": None,
        "resources": [],
        "errors": ["Discovery has not completed yet."],
        "complete": False,
    }


@app.post("/api/live/refresh", status_code=202)
def refresh_resources(owner: str = Depends(identity)):
    current = snapshot()
    if current:
        from datetime import datetime, timezone

        if (
            datetime.now(timezone.utc) - datetime.fromisoformat(current["collected_at"])
        ).total_seconds() < 60:
            return {"status": "recent-snapshot", "collected_at": current["collected_at"]}
    aws_client("lambda").invoke(
        FunctionName=os.environ["DISCOVERY_FUNCTION"], InvocationType="Event", Payload=b"{}"
    )
    return {"status": "queued"}


@app.post("/api/live/investigations", status_code=202)
def investigate_live_resource(body: LiveInvestigation, owner: str = Depends(identity)):
    try:
        evidence_for_resource(body.resource_arn)
    except ValueError as issue:
        raise HTTPException(409, str(issue)) from issue
    incident = {
        "id": str(uuid.uuid4()),
        "owner": owner,
        "scenario_id": "live-investigation",
        "source": "aws-live",
        "resource_arn": body.resource_arn,
        "status": "ANALYZING",
        "created_at": utc_now(),
        "revision": 1,
        "observations": {},
        "events": [],
        "ttl": int(time.time()) + 30 * 86400,
    }
    add_event(
        incident,
        "Live investigation requested",
        "Read-only AWS evidence. AI analysis cannot authorize real restoration or traffic cutover.",
    )
    save(incident)
    try:
        execution = start_workflow(incident)
    except Exception as issue:
        incident.update(status="FAILED", revision=2)
        add_event(incident, "Analysis scheduling unavailable", "No AWS recovery was attempted.")
        save(incident, 1)
        raise HTTPException(503, "Analysis workflow could not be started.") from issue
    return {**public_incident(incident), "execution_id": execution["executionArn"]}


@app.get("/api/dr/configuration")
def recovery_configuration(request: Request, owner: str = Depends(identity)):
    config = recovery.configuration()
    return {
        "enabled": config.get("enabled", False),
        "policy": config.get("policy", {}),
        "targets": [
            {key: item[key] for key in ("id", "resource_arn", "resource_type", "dr_region")}
            for item in config.get("targets", [])
        ],
        "can_approve": can_approve_restore(request),
        "operator_id": owner,
    }


@app.get("/api/dr/plans")
def get_restore_plans(owner: str = Depends(identity)):
    return [public_incident(plan) for plan in recovery.plans()]


@app.post("/api/dr/plans", status_code=201)
def create_restore_plan(body: CreateRestore, owner: str = Depends(identity)):
    return public_incident(recovery.create_plan(body.target_id, owner))


@app.get("/api/dr/plans/{plan_id}")
def get_restore_plan(plan_id: str, owner: str = Depends(identity)):
    return public_incident(recovery.checked_plan(plan_id))


@app.post("/api/dr/plans/{plan_id}/approve", status_code=202)
def approve_restore_plan(
    plan_id: str, body: ApproveRestore, approver: str = Depends(restore_approver)
):
    plan = recovery.checked_plan(plan_id)
    try:
        recovery.approve_plan(
            plan, approver, body.revision, body.approval_digest, body.confirmation
        )
    except ClientError as issue:
        if issue.response["Error"]["Code"] == "TransactionCanceledException":
            raise HTTPException(
                409,
                "Target is locked by another restore or the plan changed. Inspect restore history before retrying.",
            ) from issue
        raise
    try:
        workflows().start_execution(
            stateMachineArn=os.environ["RESTORE_WORKFLOW_ARN"],
            name=plan["id"],
            input=json.dumps({"plan_id": plan["id"]}),
        )
    except Exception as issue:
        logger.exception("Restore scheduling could not be confirmed for %s", plan_id)
        raise HTTPException(
            503,
            "Restore scheduling could not be confirmed. The target remains locked; inspect Step Functions and AWS Backup before taking further action.",
        ) from issue
    return public_incident(plan)


handler = Mangum(app, lifespan="off")
