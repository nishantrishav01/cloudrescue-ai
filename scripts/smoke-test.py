import argparse
import json
import secrets
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import httpx
from botocore.config import Config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", default="infrastructure/outputs.json")
    parser.add_argument("--expect-inference", action="store_true")
    parser.add_argument("--scenario", default="db-outage")
    parser.add_argument("--live-resource-arn")
    parser.add_argument("--drill-report", action="store_true")
    parser.add_argument("--verify-benchmark", action="store_true")
    parser.add_argument("--verify-live", action="store_true")
    arguments = parser.parse_args()
    outputs = json.loads(Path(arguments.outputs).read_text(encoding="utf-8"))
    stack = next(value for value in outputs.values() if "UserPoolId" in value)
    region = stack["WorkflowArn"].split(":")[3]
    expected_account = stack["WorkflowArn"].split(":")[4]
    session = boto3.Session(region_name=region)
    account = session.client("sts").get_caller_identity()["Account"]
    if account != expected_account:
        raise RuntimeError("AWS credentials do not match the deployment account.")
    workflow = session.client(
        "stepfunctions",
        config=Config(
            connect_timeout=5,
            read_timeout=30,
            retries={"mode": "adaptive", "total_max_attempts": 3},
        ),
    )
    cognito = session.client(
        "cognito-idp",
        config=Config(
            connect_timeout=5,
            read_timeout=30,
            retries={"mode": "adaptive", "total_max_attempts": 3},
        ),
    )
    username = f"cloudrescue-smoke-{uuid.uuid4().hex[:12]}@example.invalid"
    password = secrets.token_urlsafe(28) + "Aa1!"
    pool_id = stack["UserPoolId"]
    cognito.admin_create_user(
        UserPoolId=pool_id,
        Username=username,
        MessageAction="SUPPRESS",
        UserAttributes=[
            {"Name": "email", "Value": username},
            {"Name": "email_verified", "Value": "true"},
        ],
    )
    try:
        cognito.admin_set_user_password(
            UserPoolId=pool_id, Username=username, Password=password, Permanent=True
        )
        auth = cognito.admin_initiate_auth(
            UserPoolId=pool_id,
            ClientId=stack["UserPoolClientId"],
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )
        token = auth["AuthenticationResult"]["AccessToken"]
        with httpx.Client(base_url=stack["ApiUrl"], timeout=30) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/api/scenarios").status_code == 401
            client.headers["Authorization"] = f"Bearer {token}"
            scenarios = client.get("/api/scenarios")
            scenarios.raise_for_status()
            assert len(scenarios.json()) == 5
            if arguments.verify_live:
                refresh = client.post("/api/live/refresh", json={})
                refresh.raise_for_status()
                inventory = None
                for _ in range(60):
                    response = client.get("/api/live/resources")
                    response.raise_for_status()
                    inventory = response.json()
                    if inventory.get("collected_at") and inventory.get("complete"):
                        break
                    time.sleep(3)
                assert inventory and inventory["complete"], inventory
                assert inventory["account_id"] == account and inventory["region"] == region
                assert (
                    -5
                    <= time.time() - datetime.fromisoformat(inventory["collected_at"]).timestamp()
                    <= 600
                )
                assert inventory["source"] == "aws-live" and inventory["resources"]
                configuration = client.get("/api/dr/configuration")
                configuration.raise_for_status()
                scope = configuration.json()
                assert scope["enabled"] is False and scope["targets"] == []
                assert scope["policy"]["dr_region"] == "ap-southeast-1"
                assert (
                    scope["policy"]["rto_seconds"] == 3600 and scope["policy"]["rpo_seconds"] == 900
                )
                assert (
                    client.post(
                        "/api/dr/plans", json={"target_id": "unreviewed-target"}
                    ).status_code
                    == 409
                )
                assert (
                    client.post(
                        "/api/dr/plans/dr-123/approve",
                        json={
                            "revision": 1,
                            "approval_digest": "0" * 64,
                            "confirmation": "RESTORE x TO y",
                            "accept_aws_charges": True,
                        },
                    ).status_code
                    == 403
                )
                assert (
                    client.post("/api/dr/configuration", json={"enabled": True}).status_code == 405
                )
                plans = client.get("/api/dr/plans")
                plans.raise_for_status()
                assert plans.json() == []
                verification = subprocess.run(
                    ["node", "scripts/verify-live-resources.cjs"],
                    input=json.dumps(
                        {
                            "token": token,
                            "site": stack["WebsiteUrl"],
                            "expectedResources": len(inventory["resources"]),
                        }
                    ),
                    text=True,
                    capture_output=True,
                    timeout=120,
                )
                print(verification.stdout, end="", flush=True)
                if verification.returncode:
                    raise RuntimeError(verification.stderr)
                artifact = Path("artifacts/live-deployed-verification.json")
                artifact.write_text(
                    json.dumps(
                        {
                            "inventory": inventory,
                            "recovery_configuration": scope,
                            "blocked_unreviewed_target": 409,
                            "blocked_unauthorized_approval": 403,
                            "configuration_is_read_only": True,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                print(
                    json.dumps(
                        {
                            "live_verification": "passed",
                            "resource_count": len(inventory["resources"]),
                            "restore_execution": "disabled",
                            "artifact": str(artifact),
                        }
                    ),
                    flush=True,
                )
                return
            if arguments.verify_benchmark:
                benchmark_response = client.get("/api/benchmarks")
                benchmark_response.raise_for_status()
                evaluation = benchmark_response.json()["evaluation"]
                assert evaluation["case_count"] == evaluation["valid_inferences"] == 30
                verification = subprocess.run(
                    ["node", "scripts/verify-live-benchmark.cjs"],
                    input=json.dumps({"token": token, "site": stack["WebsiteUrl"]}),
                    text=True,
                    capture_output=True,
                    timeout=90,
                )
                print(verification.stdout, end="", flush=True)
                if verification.returncode:
                    raise RuntimeError(verification.stderr)
                print(
                    json.dumps(
                        {
                            "benchmark_api": "passed",
                            "case_count": evaluation["case_count"],
                            "accuracy": evaluation["accuracy"],
                        }
                    ),
                    flush=True,
                )
                return
            created = (
                client.post(
                    "/api/live/investigations", json={"resource_arn": arguments.live_resource_arn}
                )
                if arguments.live_resource_arn
                else client.post("/api/incidents", json={"scenario_id": arguments.scenario})
            )
            assert created.status_code == 202, created.text
            incident = created.json()
            incident_id = incident["id"]
            print(
                json.dumps({"incident_id": incident_id, "initial_status": incident["status"]}),
                flush=True,
            )
            deadline = time.monotonic() + (1100 if arguments.expect_inference else 120)
            last_status = None
            while time.monotonic() < deadline:
                response = client.get(f"/api/incidents/{incident_id}")
                response.raise_for_status()
                incident = response.json()
                if incident["status"] != last_status:
                    print(json.dumps({"status": incident["status"]}), flush=True)
                    last_status = incident["status"]
                if incident["status"] not in {"ANALYZING", "SIMULATING"}:
                    break
                time.sleep(4)
            assert incident["status"] in {"WAITING_APPROVAL", "ESCALATED"}, incident["status"]
            if arguments.live_resource_arn:
                assert incident["source"] == "aws-live"
                assert incident["observations"]["evidence_source"] == "aws-cloudwatch"
                assert incident["status"] == "ESCALATED"
            if arguments.expect_inference:
                assert incident.get("decision", {}).get("source") == "strands-decider", (
                    "Actual Decider result was not recorded"
                )
                assert incident.get("explanation_source") == "strands-bedrock", (
                    "Actual Bedrock explanation was not recorded"
                )
                print(
                    json.dumps(
                        {
                            "choice": incident["decision"]["choice"],
                            "confidence": incident["decision"]["confidence"],
                            "latency_ms": incident["decision"]["latency_ms"],
                            "model_id": incident["decision"]["model_id"],
                        }
                    ),
                    flush=True,
                )
            else:
                assert incident["status"] == "ESCALATED" and "decision" not in incident
            blocked_approval_status = None
            if incident["status"] == "ESCALATED":
                blocked_approval = client.post(
                    f"/api/incidents/{incident_id}/approve",
                    json={"revision": incident["revision"], "decision_digest": "0" * 64},
                )
                blocked_approval_status = blocked_approval.status_code
                assert blocked_approval_status == 409, blocked_approval.text
                assert "recovery" not in incident
            if incident["status"] == "WAITING_APPROVAL":
                approval = {
                    "revision": incident["revision"],
                    "decision_digest": incident["approval_digest"],
                }
                assert (
                    client.post(f"/api/incidents/{incident_id}/approve", json=approval).status_code
                    == 202
                )
                assert (
                    client.post(f"/api/incidents/{incident_id}/approve", json=approval).status_code
                    == 409
                )
                for _ in range(60):
                    incident = client.get(f"/api/incidents/{incident_id}").json()
                    if incident["status"] != "SIMULATING":
                        break
                    time.sleep(3)
                assert incident["status"] == "COMPLETED", incident["status"]
                assert incident["report"] and incident["report_source"] == "strands-bedrock"
            evidence_path = Path("artifacts")
            evidence_path.mkdir(exist_ok=True)
            execution = workflow.describe_execution(executionArn=created.json()["execution_id"])
            for _ in range(10):
                if execution["status"] != "RUNNING":
                    break
                time.sleep(1)
                execution = workflow.describe_execution(executionArn=created.json()["execution_id"])
            assert execution["status"] == "SUCCEEDED", execution["status"]
            elapsed_seconds = (execution["stopDate"] - execution["startDate"]).total_seconds()
            (evidence_path / f"aws-smoke-{incident_id}.json").write_text(
                json.dumps(incident, indent=2), encoding="utf-8"
            )
            if arguments.drill_report:
                started_at = execution["startDate"].astimezone(
                    timezone(timedelta(hours=5, minutes=30))
                )
                summary = {
                    "account_id": account,
                    "region": region,
                    "scenario": arguments.scenario,
                    "incident_id": incident_id,
                    "execution_arn": execution["executionArn"],
                    "workflow_status": execution["status"],
                    "incident_status": incident["status"],
                    "workflow_elapsed_seconds": elapsed_seconds,
                    "blocked_approval_http_status": blocked_approval_status,
                    "model_invoked": "decision" in incident,
                    "recovery_recorded": "recovery" in incident,
                    "scope": "Simulated incident through the deployed AWS application; no real workload outage or failover.",
                }
                (evidence_path / f"drill-{incident_id}.json").write_text(
                    json.dumps(summary, indent=2), encoding="utf-8"
                )
                lines = [
                    "# CloudRescue AI drill result",
                    "",
                    f"Date: {started_at.strftime('%d %B %Y, %H:%M:%S')} IST",
                    f"Account: {account}",
                    f"Region: {region}",
                    f"Scenario: {arguments.scenario}",
                    f"Incident: {incident_id}",
                    f"Workflow: {execution['executionArn']}",
                    "",
                    f"Workflow result: {execution['status']}",
                    f"Incident outcome: {incident['status']}",
                    f"Measured analysis workflow duration: {elapsed_seconds:.3f} seconds",
                    f"Approval attempt on escalated incident: HTTP {blocked_approval_status}",
                    "",
                    "## Scope and limitations",
                    "",
                    "This drill used simulated database evidence through the deployed authenticated API and Step Functions workflow. No production outage, real failover, DR provisioning, or recovery was initiated.",
                    "Decider is disabled. Safe escalation and rejection of approval are the expected result. No model decision or AI recovery performance is claimed.",
                    "RTO and RPO were not measured. Analysis workflow duration is not an application recovery time.",
                    "A temporary operator was used; its incidents are isolated from the permanent operator's dashboard history. The script removes the temporary operator in its cleanup step.",
                    "",
                    "## Recorded timeline",
                    "",
                    *[
                        f"- {entry['at']}: {entry['title']} - {entry['detail']}"
                        for entry in incident["events"]
                    ],
                ]
                report_path = evidence_path / f"drill-{incident_id}.md"
                report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                print(json.dumps({"report": str(report_path), **summary}), flush=True)
            print(
                json.dumps(
                    {
                        "result": "passed",
                        "final_status": incident["status"],
                        "authenticated": True,
                        "approval_replay_test": "passed"
                        if incident["status"] == "COMPLETED"
                        else "not-exercised-no-approved-playbook",
                        "blocked_approval_http_status": blocked_approval_status,
                    }
                ),
                flush=True,
            )
    finally:
        cognito.admin_delete_user(UserPoolId=pool_id, Username=username)


if __name__ == "__main__":
    main()
