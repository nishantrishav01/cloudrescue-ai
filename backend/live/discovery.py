import json
import logging
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)
LIMIT = 100
METRICS = {
    "EC2": (
        "AWS/EC2",
        "InstanceId",
        [("CPUUtilization", "Average"), ("StatusCheckFailed", "Maximum")],
    ),
    "RDS": (
        "AWS/RDS",
        "DBInstanceIdentifier",
        [("CPUUtilization", "Average"), ("DatabaseConnections", "Average")],
    ),
    "Lambda": (
        "AWS/Lambda",
        "FunctionName",
        [("Invocations", "Sum"), ("Errors", "Sum"), ("Throttles", "Sum")],
    ),
    "S3": (
        "AWS/S3",
        "BucketName",
        [("BucketSizeBytes", "Average"), ("NumberOfObjects", "Average")],
    ),
}


@lru_cache
def client(service: str, region: str | None = None):
    return boto3.client(
        service,
        region_name=region,
        config=Config(
            connect_timeout=3,
            read_timeout=10,
            retries={"mode": "adaptive", "total_max_attempts": 2},
        ),
    )


def snapshot() -> dict | None:
    storage = client("s3")
    try:
        with storage.get_object(Bucket=os.environ["LIVE_BUCKET"], Key="inventory/latest.json")[
            "Body"
        ] as body:
            return json.loads(body.read())
    except storage.exceptions.NoSuchKey:
        return None


def collect() -> dict:
    region = os.environ["AWS_REGION"]
    account = os.environ["AWS_ACCOUNT_ID"]
    result = {
        "account_id": account,
        "region": region,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "source": "aws-live",
        "resources": [],
        "errors": [],
        "truncated": False,
    }

    def append(kind, identifier, arn, state, tags=None):
        if identifier.lower().startswith(("cloudrescue", "cdk-hnb659fds")):
            return
        tags = tags or {}
        if sum(item["kind"] == kind for item in result["resources"]) >= LIMIT:
            result["truncated"] = True
            return
        result["resources"].append(
            {
                "id": identifier,
                "arn": arn,
                "kind": kind,
                "name": tags.get("Name", identifier),
                "state": state,
                "region": region,
                "tags": tags,
                "metrics": [],
                "alarms": [],
            }
        )

    def bounded(service, operation, key, **arguments):
        count = 0
        for page in client(service).get_paginator(operation).paginate(**arguments):
            for item in page.get(key, []):
                count += 1
                if count > LIMIT:
                    result["truncated"] = True
                    return
                yield item

    def ec2():
        for reservation in bounded("ec2", "describe_instances", "Reservations"):
            for instance in reservation["Instances"]:
                identifier = instance["InstanceId"]
                append(
                    "EC2",
                    identifier,
                    f"arn:aws:ec2:{region}:{account}:instance/{identifier}",
                    instance["State"]["Name"],
                    {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])},
                )

    def rds():
        for database in bounded("rds", "describe_db_instances", "DBInstances"):
            append(
                "RDS",
                database["DBInstanceIdentifier"],
                database["DBInstanceArn"],
                database["DBInstanceStatus"],
                {tag["Key"]: tag["Value"] for tag in database.get("TagList", [])},
            )

    def functions():
        for function in bounded("lambda", "list_functions", "Functions"):
            append("Lambda", function["FunctionName"], function["FunctionArn"], "discovered")

    def buckets():
        for bucket in bounded("s3", "list_buckets", "Buckets", BucketRegion=region):
            append("S3", bucket["Name"], f"arn:aws:s3:::{bucket['Name']}", "discovered")

    for service, discover in [("EC2", ec2), ("RDS", rds), ("Lambda", functions), ("S3", buckets)]:
        try:
            discover()
        except Exception:
            logger.exception("Live discovery failed for %s", service)
            result["errors"].append(
                f"{service} discovery unavailable; inventory may be incomplete."
            )
    try:
        add_metrics(result["resources"])
    except Exception:
        logger.exception("CloudWatch metrics unavailable")
        result["errors"].append(
            "CloudWatch measurement collection incomplete; missing values are not zero."
        )
    try:
        resources = {item["id"]: item for item in result["resources"]}
        for alarm in bounded(
            "cloudwatch", "describe_alarms", "MetricAlarms", AlarmTypes=["MetricAlarm"]
        ):
            dimensions = alarm.get("Dimensions", []) + [
                dimension
                for metric in alarm.get("Metrics", [])
                for dimension in metric.get("MetricStat", {})
                .get("Metric", {})
                .get("Dimensions", [])
            ]
            for identifier in {dimension["Value"] for dimension in dimensions}:
                if identifier in resources:
                    resources[identifier]["alarms"].append(
                        {
                            "name": alarm["AlarmName"],
                            "state": alarm["StateValue"],
                            "updated_at": alarm["StateUpdatedTimestamp"].isoformat(),
                        }
                    )
    except Exception:
        logger.exception("CloudWatch alarm discovery unavailable")
        result["errors"].append("CloudWatch alarm discovery incomplete.")
    result["complete"] = not result["errors"] and not result["truncated"]
    return result


def add_metrics(resources: list[dict]):
    now = datetime.now(timezone.utc)
    queries = []
    targets = {}
    for resource in resources:
        namespace, dimension, definitions = METRICS[resource["kind"]]
        for name, statistic in definitions:
            identifier = f"metric{len(queries)}"
            dimensions = [{"Name": dimension, "Value": resource["id"]}]
            if resource["kind"] == "S3":
                dimensions.append(
                    {
                        "Name": "StorageType",
                        "Value": "StandardStorage"
                        if name == "BucketSizeBytes"
                        else "AllStorageTypes",
                    }
                )
            period = 86400 if resource["kind"] == "S3" else 300
            value = {
                "name": name,
                "value": None,
                "timestamp": None,
                "status": "missing",
                "period_seconds": period,
                "statistic": statistic,
            }
            resource["metrics"].append(value)
            targets[identifier] = value
            queries.append(
                {
                    "Id": identifier,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": namespace,
                            "MetricName": name,
                            "Dimensions": dimensions,
                        },
                        "Period": period,
                        "Stat": statistic,
                    },
                    "ReturnData": True,
                }
            )
    for start in range(0, len(queries), 400):
        batch = queries[start : start + 400]
        for page in (
            client("cloudwatch")
            .get_paginator("get_metric_data")
            .paginate(
                MetricDataQueries=batch,
                StartTime=now
                - (
                    timedelta(days=2)
                    if any(query["MetricStat"]["Period"] == 86400 for query in batch)
                    else timedelta(minutes=15)
                ),
                EndTime=now,
                ScanBy="TimestampDescending",
            )
        ):
            for measurement in page.get("MetricDataResults", []):
                target = targets[measurement["Id"]]
                if measurement.get("Values") and target["value"] is None:
                    timestamp = measurement["Timestamps"][0]
                    age = (now - timestamp).total_seconds()
                    target.update(
                        value=measurement["Values"][0],
                        timestamp=timestamp.isoformat(),
                        status="fresh"
                        if -5 <= age <= max(900, target["period_seconds"] * 2)
                        and measurement.get("StatusCode") == "Complete"
                        else "stale-or-partial",
                    )


def handler(event, context):
    result = collect()
    client("s3").put_object(
        Bucket=os.environ["LIVE_BUCKET"],
        Key="inventory/latest.json",
        Body=json.dumps(result).encode(),
        ContentType="application/json",
    )
    return {"resources": len(result["resources"]), "complete": result["complete"]}


def evidence_for_resource(resource_arn: str) -> tuple[dict, dict]:
    data = snapshot()
    age = (
        (datetime.now(timezone.utc) - datetime.fromisoformat(data["collected_at"])).total_seconds()
        if data
        else None
    )
    if age is None or not -5 <= age <= 600:
        raise ValueError("Refresh live discovery before requesting investigation.")
    resource = next((item for item in data["resources"] if item["arn"] == resource_arn), None)
    if not resource:
        raise ValueError("Resource is not in the current account/region inventory.")
    observations = {
        "evidence_source": "aws-cloudwatch",
        "observed_at": data["collected_at"],
        "account_id": data["account_id"],
        "region": data["region"],
        "resource_arn": resource_arn,
        "resource_type": resource["kind"],
        "resource_state": resource["state"],
        "cloudwatch_metrics_json": json.dumps(resource["metrics"], sort_keys=True),
        "alarms_json": json.dumps(resource["alarms"], sort_keys=True),
        "prerequisites": "Standby, capacity, application health, replication and recovery-point consistency are not established. Read-only investigation only.",
    }
    return observations, resource
