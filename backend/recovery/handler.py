import logging
import os
import time
from datetime import datetime

from backend.common.safety import canonical_hash
from backend.common.store import ConflictError, add_event, read, save, utc_now
from backend.live.discovery import client
from backend.recovery.service import (
    TERMINAL,
    RecoveryBlocked,
    checked_plan,
    configuration,
    target_for,
    validate_plan,
)

logger = logging.getLogger(__name__)


def handler(event, context):
    if event.get("detail", {}).get("status") in {"FAILED", "TIMED_OUT", "ABORTED"}:
        event = {"plan_id": event["detail"]["executionArn"].rsplit(":", 1)[-1], "failure": True}
    plan = checked_plan(event["plan_id"])
    if plan["status"] in TERMINAL:
        return {"terminal": True}
    if plan["status"] not in {"QUEUED", "RESTORING"}:
        raise RecoveryBlocked("Restore has no accepted approval.")
    revision = plan["revision"]
    try:
        if event.get("failure"):
            raise RecoveryBlocked(
                "Restore workflow failed or timed out. Inspect AWS Backup jobs; an unconfirmed job may still run and incur costs."
            )
        config, target = target_for(plan["target_id"], require_enabled=True)
        lock = read(f"dr-lock:{plan['target_id']}")
        if (
            not lock
            or lock.get("plan_id") != plan["id"]
            or not plan.get("approved_by")
            or plan["approved_by"] == plan["created_by"]
            or canonical_hash(configuration()) != plan["configuration_hash"]
        ):
            raise RecoveryBlocked("Restore lock, independent approval or configuration is invalid.")
        backup = client("backup", target["dr_region"])
        if not plan.get("restore_job_id"):
            validate_plan(plan)
            response = backup.start_restore_job(
                RecoveryPointArn=plan["recovery_point"]["arn"],
                Metadata=plan["metadata"],
                IamRoleArn=target["restore_role_arn"],
                IdempotencyToken=plan["id"],
                ResourceType=target["resource_type"],
                CopySourceTagsToRestoredResource=True,
            )
            plan.update(
                status="RESTORING",
                restore_job_id=response["RestoreJobId"],
                restore_requested_at=utc_now(),
            )
            add_event(
                plan,
                "AWS Backup restore started",
                f"Actual AWS restore job {plan['restore_job_id']}; source resources and traffic unchanged.",
            )
        else:
            job = backup.describe_restore_job(RestoreJobId=plan["restore_job_id"])
            plan["backup_job_status"] = job["Status"]
            if (
                job.get("AccountId") != config["policy"]["account_id"]
                or job.get("RecoveryPointArn") != plan["recovery_point"]["arn"]
            ):
                raise RecoveryBlocked(
                    "AWS restore job identity does not match the approved recovery point."
                )
            if job["Status"] == "COMPLETED":
                plan.update(
                    status="RESTORED_PENDING_VALIDATION",
                    created_resource_arn=job.get("CreatedResourceArn"),
                    restore_completed_at=utc_now(),
                )
                if job.get("CompletionDate") and job.get("CreationDate"):
                    plan["infrastructure_restore_seconds"] = (
                        job["CompletionDate"] - job["CreationDate"]
                    ).total_seconds()
                add_event(
                    plan,
                    "Infrastructure restored; cutover not performed",
                    "Application health, data consistency, actual RPO and customer-facing RTO must be validated before separate traffic cutover approval.",
                )
            elif job["Status"] in {"FAILED", "ABORTED"}:
                plan["status"] = "FAILED"
                add_event(
                    plan,
                    "AWS restore job failed",
                    job.get("StatusMessage", "Inspect AWS Backup restore history."),
                )
            elif (
                time.time() - datetime.fromisoformat(plan["restore_requested_at"]).timestamp()
                > config["policy"]["rto_seconds"]
            ):
                raise RecoveryBlocked(
                    "The one-hour recovery objective has been exceeded. The AWS job is not cancelled; investigate it and any billable restored resources manually."
                )
    except Exception:
        logger.exception("Restore requires operator attention: %s", plan["id"])
        plan["status"] = "ATTENTION_REQUIRED"
        add_event(
            plan,
            "Restore needs investigation",
            "No further restore or cutover will be attempted. Inspect AWS Backup jobs, logs and the exclusive target lock; a job may still be active and billable.",
        )
    plan["revision"] = revision + 1
    try:
        save(plan, revision)
    except ConflictError:
        return {"terminal": False}
    if plan["status"] in {"ATTENTION_REQUIRED", "FAILED"} and os.environ.get("ALERTS_TOPIC_ARN"):
        client("sns").publish(
            TopicArn=os.environ["ALERTS_TOPIC_ARN"],
            Subject="CloudRescue restore requires investigation",
            Message=f"Plan {plan['id']} is {plan['status']}. Inspect AWS Backup jobs, logs, resource charges and the exclusive target lock. No automatic cutover or cleanup is authorized.",
        )
    return {"terminal": plan["status"] in TERMINAL}
