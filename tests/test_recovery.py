import copy
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from backend.common.safety import canonical_hash
from backend.recovery import handler as executor
from backend.recovery import service


@pytest.fixture
def scope(monkeypatch):
    config = {
        "enabled": True,
        "policy": {
            "account_id": "123456789012",
            "primary_region": "ap-south-1",
            "dr_region": "ap-southeast-1",
            "resource_types": ["EC2", "RDS", "Lambda"],
            "rpo_seconds": 900,
            "rto_seconds": 3600,
            "approval_ttl_seconds": 600,
            "require_different_approver": True,
            "automatic_cutover": False,
        },
        "targets": [
            {
                "id": "app-compute",
                "resource_arn": "arn:aws:ec2:ap-south-1:123456789012:instance/i-1234",
                "resource_type": "EC2",
                "dr_region": "ap-southeast-1",
                "backup_vault_name": "reviewed-vault",
                "restore_role_arn": "arn:aws:iam::123456789012:role/reviewed-restore",
                "restore_metadata": {
                    "SubnetId": "subnet-1234",
                    "SecurityGroupIds": '["sg-1234"]',
                    "InstanceType": "t3.small",
                    "RequireIMDSv2": "true",
                },
            }
        ],
    }
    monkeypatch.setattr(service, "configuration", lambda: copy.deepcopy(config))
    monkeypatch.setattr(service, "validate_network", lambda target: None)
    monkeypatch.setattr(executor, "configuration", lambda: copy.deepcopy(config))
    return config


def point(scope, **overrides):
    return {
        "RecoveryPointArn": "arn:aws:backup:ap-southeast-1:123456789012:recovery-point:point1",
        "ResourceArn": scope["targets"][0]["resource_arn"],
        "ResourceType": "EC2",
        "Status": "COMPLETED",
        "IsEncrypted": True,
        "CreationDate": datetime.now(timezone.utc) - timedelta(minutes=2),
        **overrides,
    }


def bind_point(monkeypatch, recovery_point):
    backup = SimpleNamespace(
        describe_recovery_point=lambda **arguments: recovery_point,
        get_paginator=lambda operation: SimpleNamespace(
            paginate=lambda **arguments: [{"RecoveryPoints": [recovery_point]}]
        ),
    )
    monkeypatch.setattr(service, "client", lambda *args: backup)


def make_plan(monkeypatch, scope):
    bind_point(monkeypatch, point(scope))
    monkeypatch.setattr(service, "save", lambda *args: None)
    return service.create_plan("app-compute", "planner")


@pytest.mark.parametrize(
    "override",
    [
        {"IsEncrypted": False},
        {"Status": "PARTIAL"},
        {"CreationDate": datetime.now(timezone.utc) - timedelta(minutes=16)},
        {"CreationDate": datetime.now(timezone.utc) + timedelta(minutes=1)},
        {"ResourceArn": "another-resource"},
        {"RecoveryPointArn": "arn:aws:backup:ap-south-1:123456789012:recovery-point:wrong-region"},
        {
            "RecoveryPointArn": "arn:aws:backup:ap-southeast-1:999999999999:recovery-point:wrong-account"
        },
    ],
)
def test_recovery_point_must_be_fresh_encrypted_and_in_scope(monkeypatch, scope, override):
    bind_point(monkeypatch, point(scope, **override))
    with pytest.raises(service.RecoveryBlocked):
        service.recovery_point(scope["targets"][0], scope["policy"])


def test_disabled_scope_and_unreviewed_metadata_are_blocked(scope):
    scope["enabled"] = False
    with pytest.raises(service.RecoveryBlocked, match="disabled"):
        service.target_for("app-compute", require_enabled=True)
    scope["enabled"] = True
    scope["targets"][0]["restore_metadata"]["UserData"] = "unreviewed executable data"
    with pytest.raises(service.RecoveryBlocked, match="metadata"):
        service.target_for("app-compute")


def test_approval_requires_independent_operator_and_exact_plan(monkeypatch, scope):
    plan = make_plan(monkeypatch, scope)
    for owner, revision, approval_digest, confirmation in [
        ("planner", 1, plan["approval_digest"], plan["confirmation"]),
        ("approver", 2, plan["approval_digest"], plan["confirmation"]),
        ("approver", 1, "0" * 64, plan["confirmation"]),
        ("approver", 1, plan["approval_digest"], "another region"),
    ]:
        with pytest.raises(service.RecoveryBlocked):
            service.approve_plan(plan, owner, revision, approval_digest, confirmation)
    plan["metadata"]["InstanceType"] = "m5.24xlarge"
    with pytest.raises(service.RecoveryBlocked, match="changed"):
        service.validate_plan(plan)


def test_configuration_changes_or_expiry_invalidate_plan(monkeypatch, scope):
    plan = make_plan(monkeypatch, scope)
    scope["policy"]["rto_seconds"] = 7200
    with pytest.raises(service.RecoveryBlocked, match="changed"):
        service.validate_plan(plan)
    scope["policy"]["rto_seconds"] = 3600
    plan["expires_at"] = int(time.time()) - 1
    plan["approval_digest"] = service.digest(plan)
    with pytest.raises(service.RecoveryBlocked, match="expired"):
        service.validate_plan(plan)


def test_approval_atomically_locks_target_and_plan(monkeypatch, scope):
    plan = make_plan(monkeypatch, scope)
    transactions = []
    monkeypatch.setattr(
        service,
        "table",
        lambda: SimpleNamespace(
            name="incidents",
            meta=SimpleNamespace(
                client=SimpleNamespace(
                    transact_write_items=lambda **arguments: transactions.append(arguments)
                )
            ),
        ),
    )
    service.approve_plan(
        plan, "independent-approver", 1, plan["approval_digest"], plan["confirmation"]
    )
    assert plan["status"] == "QUEUED" and plan["revision"] == 2
    puts = transactions[0]["TransactItems"]
    assert puts[0]["Put"]["Item"]["id"] == "dr-lock:app-compute"
    assert puts[0]["Put"]["ConditionExpression"] == "attribute_not_exists(id)"
    assert "WAITING_APPROVAL" in puts[1]["Put"]["ExpressionAttributeValues"].values()
    assert puts[1]["Put"]["Item"]["configuration_hash"] == canonical_hash(scope)


def test_replay_or_lock_conflict_does_not_start_a_restore(monkeypatch, scope):
    plan = make_plan(monkeypatch, scope)

    def conflict(**arguments):
        raise ClientError({"Error": {"Code": "TransactionCanceledException"}}, "TransactWriteItems")

    monkeypatch.setattr(
        service,
        "table",
        lambda: SimpleNamespace(
            name="incidents",
            meta=SimpleNamespace(client=SimpleNamespace(transact_write_items=conflict)),
        ),
    )
    with pytest.raises(ClientError):
        service.approve_plan(plan, "approver", 1, plan["approval_digest"], plan["confirmation"])


def test_executor_uses_idempotent_real_aws_backup_request(monkeypatch, scope):
    plan = make_plan(monkeypatch, scope)
    plan.update(status="QUEUED", approved_by="approver", revision=2)
    monkeypatch.setattr(executor, "checked_plan", lambda plan_id: plan)
    monkeypatch.setattr(executor, "read", lambda key: {"plan_id": plan["id"]})
    monkeypatch.setattr(executor, "save", lambda *args: None)
    calls = []

    def start(**arguments):
        calls.append(arguments)
        return {"RestoreJobId": "aws-job"}

    monkeypatch.setattr(executor, "client", lambda *args: SimpleNamespace(start_restore_job=start))
    assert executor.handler({"plan_id": plan["id"]}, None) == {"terminal": False}
    assert calls[0]["IdempotencyToken"] == plan["id"]
    assert calls[0]["CopySourceTagsToRestoredResource"] is True
    assert calls[0]["IamRoleArn"] == scope["targets"][0]["restore_role_arn"]
    assert calls[0]["Metadata"] == plan["metadata"]
    assert plan["status"] == "RESTORING" and plan["restore_job_id"] == "aws-job"


def test_completed_backup_restore_is_not_reported_as_application_recovery(monkeypatch, scope):
    plan = make_plan(monkeypatch, scope)
    plan.update(status="RESTORING", approved_by="approver", restore_job_id="aws-job", revision=3)
    monkeypatch.setattr(executor, "checked_plan", lambda plan_id: plan)
    monkeypatch.setattr(executor, "read", lambda key: {"plan_id": plan["id"]})
    monkeypatch.setattr(executor, "save", lambda *args: None)
    monkeypatch.setattr(
        executor,
        "client",
        lambda *args: SimpleNamespace(
            describe_restore_job=lambda **arguments: {
                "AccountId": "123456789012",
                "RecoveryPointArn": plan["recovery_point"]["arn"],
                "Status": "COMPLETED",
                "CreatedResourceArn": "arn:aws:ec2:ap-southeast-1:123456789012:instance/i-restored",
            }
        ),
    )
    assert executor.handler({"plan_id": plan["id"]}, None) == {"terminal": True}
    assert plan["status"] == "RESTORED_PENDING_VALIDATION"
    assert plan["application_rto_seconds"] is None and plan["actual_data_rpo_seconds"] is None


def test_configuration_manifest_is_fail_closed_for_account_mismatch(monkeypatch):
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("RECOVERY_CONFIG", json.dumps({"policy": {"account_id": "999999999999"}}))
    monkeypatch.delenv("RECOVERY_CONFIG_BUCKET", raising=False)
    with pytest.raises(service.RecoveryBlocked, match="account"):
        service.configuration()


def test_rds_plans_generate_distinct_private_destination_databases(monkeypatch, scope):
    target = scope["targets"][0]
    target.update(
        resource_type="RDS",
        resource_arn="arn:aws:rds:ap-south-1:123456789012:db:source-db",
        restore_metadata={
            "DBSubnetGroupName": "private-dr",
            "DBInstanceClass": "db.t3.small",
            "VpcSecurityGroupIds": '["sg-1234"]',
            "PubliclyAccessible": "false",
            "DeletionProtection": "true",
        },
    )
    bind_point(
        monkeypatch,
        point(
            scope,
            ResourceType="RDS",
            RecoveryPointArn="arn:aws:rds:ap-southeast-1:123456789012:snapshot:reviewed",
        ),
    )
    monkeypatch.setattr(service, "save", lambda *args: None)
    first = service.create_plan(target["id"], "planner")
    second = service.create_plan(target["id"], "planner")
    assert first["metadata"]["DBInstanceIdentifier"] != second["metadata"]["DBInstanceIdentifier"]
    assert first["metadata"]["DBInstanceIdentifier"].startswith("cloudrescue-dr-")
    assert first["metadata"]["PubliclyAccessible"] == "false"
    assert first["metadata"]["DeletionProtection"] == "true"


def test_missing_target_lock_blocks_executor_before_any_restore_call(monkeypatch, scope):
    plan = make_plan(monkeypatch, scope)
    plan.update(status="QUEUED", approved_by="approver", revision=2)
    monkeypatch.setattr(executor, "checked_plan", lambda plan_id: plan)
    monkeypatch.setattr(executor, "read", lambda key: None)
    monkeypatch.setattr(executor, "save", lambda *args: None)
    monkeypatch.delenv("ALERTS_TOPIC_ARN", raising=False)

    def forbidden(*args):
        raise AssertionError("AWS client must not be reached without the target lock")

    monkeypatch.setattr(executor, "client", forbidden)
    assert executor.handler({"plan_id": plan["id"]}, None) == {"terminal": True}
    assert plan["status"] == "ATTENTION_REQUIRED"
