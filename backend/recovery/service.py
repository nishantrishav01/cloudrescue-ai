import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from boto3.dynamodb.conditions import Attr, ConditionExpressionBuilder

from backend.common.safety import canonical_hash
from backend.common.store import add_event, list_for_owner, read, save, table, utc_now
from backend.live.discovery import client

OWNER = "system:dr-plans"
TERMINAL = {"RESTORED_PENDING_VALIDATION", "ATTENTION_REQUIRED", "FAILED"}
ALLOWED_METADATA = {
    "EC2": {
        "InstanceType",
        "SubnetId",
        "SecurityGroupIds",
        "RequireIMDSv2",
        "IamInstanceProfileName",
        "BlockDeviceMappings",
        "DisableApiTermination",
    },
    "RDS": {
        "DBInstanceClass",
        "DBSubnetGroupName",
        "VpcSecurityGroupIds",
        "PubliclyAccessible",
        "DeletionProtection",
        "MultiAZ",
        "Port",
        "OptionGroupName",
        "DBParameterGroupName",
    },
}


class RecoveryBlocked(ValueError):
    pass


def configuration() -> dict:
    if os.environ.get("RECOVERY_CONFIG_BUCKET"):
        with client("s3").get_object(
            Bucket=os.environ["RECOVERY_CONFIG_BUCKET"], Key=os.environ["RECOVERY_CONFIG_KEY"]
        )["Body"] as body:
            value = json.loads(body.read())
    else:
        value = json.loads(os.environ.get("RECOVERY_CONFIG", '{"enabled":false,"targets":[]}'))
    policy = value.get("policy", {})
    if policy and (
        policy.get("account_id") != os.environ.get("AWS_ACCOUNT_ID")
        or policy.get("require_different_approver") is not True
        or policy.get("automatic_cutover") is not False
    ):
        raise RecoveryBlocked("Recovery policy account or approval boundary is invalid.")
    return value


def target_for(identifier: str, require_enabled: bool = False) -> tuple[dict, dict]:
    config = configuration()
    if require_enabled and config.get("enabled") is not True:
        raise RecoveryBlocked("Real restoration is disabled in the approved configuration.")
    target = next((item for item in config.get("targets", []) if item["id"] == identifier), None)
    if not target:
        raise RecoveryBlocked("No reviewed recovery target is configured for this resource.")
    policy = config["policy"]
    source = target["resource_arn"].split(":", 5)
    if (
        len(source) != 6
        or source[4] != policy["account_id"]
        or source[3] != policy["primary_region"]
    ):
        raise RecoveryBlocked("Source resource is outside the approved account/region.")
    if (
        target["resource_type"] not in ALLOWED_METADATA
        or target["resource_type"] not in policy["resource_types"]
    ):
        raise RecoveryBlocked(
            "This resource requires a service-specific replication or IaC runbook."
        )
    if target["dr_region"] != policy["dr_region"] or target["dr_region"] == source[3]:
        raise RecoveryBlocked("Restore destination is outside the configured DR region.")
    if not re.fullmatch(
        rf"arn:aws:iam::{policy['account_id']}:role/[A-Za-z0-9_+=,.@/-]+",
        target["restore_role_arn"],
    ):
        raise RecoveryBlocked("Restore service role must be explicitly configured in this account.")
    metadata = target["restore_metadata"]
    if (
        not metadata
        or set(metadata) - ALLOWED_METADATA[target["resource_type"]]
        or any(not isinstance(value, str) for value in metadata.values())
    ):
        raise RecoveryBlocked("Only reviewed service-specific string restore metadata is allowed.")
    if target["resource_type"] == "EC2":
        if metadata.get("RequireIMDSv2") != "true" or not re.fullmatch(
            r"subnet-[a-f0-9]+", metadata.get("SubnetId", "")
        ):
            raise RecoveryBlocked("EC2 restores require a destination subnet and IMDSv2.")
        validate_groups(metadata.get("SecurityGroupIds", ""))
    else:
        if (
            not metadata.get("DBSubnetGroupName")
            or metadata.get("PubliclyAccessible") != "false"
            or metadata.get("DeletionProtection") != "true"
        ):
            raise RecoveryBlocked(
                "RDS restores require a DR subnet group, private access and deletion protection."
            )
        validate_groups(metadata.get("VpcSecurityGroupIds", ""))
    return config, target


def validate_groups(encoded: str) -> list[str]:
    groups = json.loads(encoded or "[]")
    if (
        not isinstance(groups, list)
        or not groups
        or any(
            not isinstance(group, str) or not re.fullmatch(r"sg-[a-f0-9]+", group)
            for group in groups
        )
    ):
        raise RecoveryBlocked("Explicit destination security groups are required.")
    return groups


def recovery_point(target: dict, policy: dict, arn: str | None = None) -> dict:
    backup = client("backup", target["dr_region"])
    if arn:
        points = [
            backup.describe_recovery_point(
                BackupVaultName=target["backup_vault_name"], RecoveryPointArn=arn
            )
        ]
    else:
        points = [
            point
            for page in backup.get_paginator("list_recovery_points_by_backup_vault").paginate(
                BackupVaultName=target["backup_vault_name"],
                ByResourceArn=target["resource_arn"],
                ByResourceType=target["resource_type"],
                ByCreatedAfter=datetime.now(timezone.utc)
                - timedelta(seconds=policy["rpo_seconds"]),
            )
            for point in page.get("RecoveryPoints", [])
        ]
        points.sort(key=lambda point: point["CreationDate"], reverse=True)
    now = datetime.now(timezone.utc)
    for point in points:
        parts = point["RecoveryPointArn"].split(":", 5)
        created = point["CreationDate"]
        age = (now - created).total_seconds()
        if (
            point.get("Status") == "COMPLETED"
            and point.get("IsEncrypted") is True
            and not point.get("IsContinuous")
            and point.get("ResourceArn") == target["resource_arn"]
            and point.get("ResourceType") == target["resource_type"]
            and len(parts) == 6
            and parts[3] == target["dr_region"]
            and parts[4] == policy["account_id"]
            and 0 <= age <= policy["rpo_seconds"]
        ):
            return {
                "arn": point["RecoveryPointArn"],
                "created_at": created.isoformat(),
                "backup_age_seconds_at_plan": round(age, 2),
                "encrypted": True,
            }
    raise RecoveryBlocked(
        "No completed encrypted destination recovery point within the configured 15-minute backup-age limit. Actual data RPO still requires validation."
    )


def validate_network(target: dict):
    metadata = target["restore_metadata"]
    ec2 = client("ec2", target["dr_region"])
    groups = validate_groups(
        metadata.get("SecurityGroupIds", metadata.get("VpcSecurityGroupIds", ""))
    )
    security_groups = ec2.describe_security_groups(GroupIds=groups)["SecurityGroups"]
    if len(security_groups) != len(groups) or any(
        rule.get("CidrIp") == "0.0.0.0/0" or rule.get("CidrIpv6") == "::/0"
        for group in security_groups
        for permission in group.get("IpPermissions", [])
        for rule in permission.get("IpRanges", []) + permission.get("Ipv6Ranges", [])
    ):
        raise RecoveryBlocked("Destination security groups must not permit public inbound access.")
    if target["resource_type"] == "EC2":
        subnet = ec2.describe_subnets(SubnetIds=[metadata["SubnetId"]])["Subnets"][0]
        if subnet.get("MapPublicIpOnLaunch") or any(
            group["VpcId"] != subnet["VpcId"] for group in security_groups
        ):
            raise RecoveryBlocked(
                "EC2 restoration requires matching private destination networking."
            )
    else:
        subnet_group = client("rds", target["dr_region"]).describe_db_subnet_groups(
            DBSubnetGroupName=metadata["DBSubnetGroupName"]
        )["DBSubnetGroups"][0]
        if subnet_group["SubnetGroupStatus"] != "Complete" or any(
            group["VpcId"] != subnet_group["VpcId"] for group in security_groups
        ):
            raise RecoveryBlocked("RDS destination subnet group and security groups must match.")


def digest(plan: dict) -> str:
    return canonical_hash(
        {
            key: plan[key]
            for key in (
                "id",
                "created_by",
                "target_id",
                "configuration_hash",
                "recovery_point",
                "metadata",
                "expires_at",
            )
        }
    )


def create_plan(target_id: str, owner: str) -> dict:
    config, target = target_for(target_id)
    validate_network(target)
    identifier = f"dr-{uuid.uuid4()}"
    metadata = dict(target["restore_metadata"])
    if target["resource_type"] == "RDS":
        metadata["DBInstanceIdentifier"] = f"cloudrescue-dr-{identifier[3:23]}"
    plan = {
        "id": identifier,
        "owner": OWNER,
        "created_by": owner,
        "created_at": utc_now(),
        "target_id": target_id,
        "resource_type": target["resource_type"],
        "source_arn": target["resource_arn"],
        "dr_region": target["dr_region"],
        "configuration_hash": canonical_hash(config),
        "recovery_point": recovery_point(target, config["policy"]),
        "metadata": metadata,
        "expires_at": int(time.time()) + config["policy"]["approval_ttl_seconds"],
        "status": "WAITING_APPROVAL",
        "revision": 1,
        "confirmation": f"RESTORE {target_id} TO {target['dr_region']}",
        "events": [],
        "application_rto_seconds": None,
        "actual_data_rpo_seconds": None,
    }
    plan["approval_digest"] = digest(plan)
    add_event(
        plan,
        "Restore plan prepared",
        "Real, billable infrastructure restore only. No DNS cutover, source shutdown or application recovery is authorized.",
    )
    save(plan)
    return plan


def checked_plan(plan_id: str) -> dict:
    plan = read(plan_id) if re.fullmatch(r"dr-[a-f0-9-]{36}", plan_id) else None
    if not plan or plan.get("owner") != OWNER:
        raise RecoveryBlocked("Restore plan not found.")
    return plan


def validate_plan(plan: dict, require_enabled: bool = True):
    config, target = target_for(plan["target_id"], require_enabled)
    if (
        canonical_hash(config) != plan["configuration_hash"]
        or digest(plan) != plan["approval_digest"]
    ):
        raise RecoveryBlocked("Plan or approved configuration changed; prepare a new plan.")
    if time.time() > plan["expires_at"]:
        raise RecoveryBlocked(
            "Restore approval expired; prepare a new plan with fresh recovery-point evidence."
        )
    point = recovery_point(target, config["policy"], plan["recovery_point"]["arn"])
    if point["created_at"] != plan["recovery_point"]["created_at"]:
        raise RecoveryBlocked("Recovery-point evidence changed.")
    validate_network(target)
    return config, target


def approve_plan(
    plan: dict, approver: str, expected_revision: int, expected_digest: str, confirmation: str
):
    if (
        plan["status"] != "WAITING_APPROVAL"
        or plan["revision"] != expected_revision
        or expected_digest != plan["approval_digest"]
        or confirmation != plan["confirmation"]
    ):
        raise RecoveryBlocked(
            "Approval must match the pending plan, revision, digest and destination confirmation."
        )
    if approver == plan["created_by"]:
        raise RecoveryBlocked("A different operator must approve this restore plan.")
    validate_plan(plan)
    plan.update(
        status="QUEUED", approved_by=approver, approved_at=utc_now(), revision=expected_revision + 1
    )
    add_event(
        plan,
        "Restore approved",
        "Independent authorized operator approved this exact plan and acknowledged AWS charges.",
    )
    lock = {"id": f"dr-lock:{plan['target_id']}", "plan_id": plan["id"], "created_at": utc_now()}
    builder = ConditionExpressionBuilder()
    condition = builder.build_expression(
        Attr("revision").eq(expected_revision) & Attr("status").eq("WAITING_APPROVAL")
    )
    storage = table()
    storage.meta.client.transact_write_items(
        TransactItems=[
            {
                "Put": {
                    "TableName": storage.name,
                    "Item": lock,
                    "ConditionExpression": "attribute_not_exists(id)",
                }
            },
            {
                "Put": {
                    "TableName": storage.name,
                    "Item": json.loads(json.dumps(plan), parse_float=Decimal),
                    "ConditionExpression": condition.condition_expression,
                    "ExpressionAttributeNames": condition.attribute_name_placeholders,
                    "ExpressionAttributeValues": condition.attribute_value_placeholders,
                }
            },
        ]
    )
    return plan


def plans() -> list[dict]:
    return list_for_owner(OWNER)
