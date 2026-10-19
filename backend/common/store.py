import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache

import boto3
from botocore.config import Config


class ConflictError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_event(incident: dict, title: str, detail: str) -> None:
    incident["events"].append({"at": utc_now(), "title": title, "detail": detail})


@lru_cache
def table():
    return boto3.resource(
        "dynamodb",
        config=Config(
            retries={"mode": "adaptive", "total_max_attempts": 3},
            connect_timeout=3,
            read_timeout=10,
        ),
    ).Table(os.environ["INCIDENT_TABLE"])


def read(incident_id: str) -> dict | None:
    item = table().get_item(Key={"id": incident_id}, ConsistentRead=True).get("Item")
    return (
        json.loads(
            json.dumps(
                item,
                default=lambda value: float(value) if isinstance(value, Decimal) else str(value),
            )
        )
        if item
        else None
    )


def save(incident: dict, expected_revision: int | None = None) -> None:
    item = json.loads(json.dumps(incident), parse_float=Decimal)
    arguments = {"Item": item, "ConditionExpression": "attribute_not_exists(id)"}
    if expected_revision is not None:
        arguments.update(
            ConditionExpression="revision = :revision",
            ExpressionAttributeValues={":revision": Decimal(str(expected_revision))},
        )
    try:
        table().put_item(**arguments)
    except table().meta.client.exceptions.ConditionalCheckFailedException as issue:
        raise ConflictError("Incident changed. Refresh before continuing.") from issue


def list_for_owner(owner: str) -> list[dict]:
    response = table().query(
        IndexName="owner-created",
        KeyConditionExpression="#owner = :owner",
        ExpressionAttributeNames={"#owner": "owner"},
        ExpressionAttributeValues={":owner": owner},
        ScanIndexForward=False,
        Limit=50,
    )
    return json.loads(
        json.dumps(
            response.get("Items", []),
            default=lambda value: float(value) if isinstance(value, Decimal) else str(value),
        )
    )
