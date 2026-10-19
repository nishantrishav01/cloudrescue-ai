import json
import os
import time
import uuid
from functools import lru_cache

import boto3
from botocore.config import Config


@lru_cache
def clients():
    config = Config(
        connect_timeout=5, read_timeout=20, retries={"mode": "adaptive", "total_max_attempts": 3}
    )
    return boto3.client("ecs", config=config), boto3.client("s3", config=config)


def run_job(cases: list[dict], timeout_seconds: int = 780) -> dict:
    ecs, s3 = clients()
    bucket = os.environ["INFERENCE_BUCKET"]
    job_id = str(uuid.uuid4())
    s3.put_object(
        Bucket=bucket,
        Key=f"requests/{job_id}.json",
        Body=json.dumps({"cases": cases}).encode(),
        ContentType="application/json",
    )
    started = time.perf_counter()
    response = ecs.run_task(
        cluster=os.environ["FARGATE_CLUSTER"],
        taskDefinition=os.environ["FARGATE_TASK_DEFINITION"],
        launchType="FARGATE",
        platformVersion="LATEST",
        propagateTags="TASK_DEFINITION",
        enableECSManagedTags=True,
        clientToken=job_id,
        startedBy="cloudrescue-ai",
        count=1,
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": os.environ["FARGATE_SUBNETS"].split(","),
                "securityGroups": [os.environ["FARGATE_SECURITY_GROUP"]],
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {"name": "DeciderWorker", "environment": [{"name": "JOB_ID", "value": job_id}]}
            ]
        },
    )
    if response.get("failures") or not response.get("tasks"):
        raise RuntimeError(f"Inference task could not start: {response.get('failures')}")
    task_arn = response["tasks"][0]["taskArn"]
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            try:
                with s3.get_object(Bucket=bucket, Key=f"results/{job_id}.json")["Body"] as body:
                    result = json.loads(body.read())
                result["job_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
                result["task_arn"] = task_arn
                if result.get("error"):
                    raise RuntimeError(f"Decider task failed: {result['error']}")
                return result
            except s3.exceptions.NoSuchKey:
                task = ecs.describe_tasks(cluster=os.environ["FARGATE_CLUSTER"], tasks=[task_arn])[
                    "tasks"
                ][0]
                if task["lastStatus"] == "STOPPED":
                    raise RuntimeError(
                        f"Inference task stopped without a result: {task.get('stoppedReason')}; {task.get('containers')}"
                    )
                time.sleep(4)
        raise TimeoutError("Decider task exceeded its bounded inference deadline.")
    finally:
        final_task = ecs.describe_tasks(cluster=os.environ["FARGATE_CLUSTER"], tasks=[task_arn])[
            "tasks"
        ][0]
        if final_task["lastStatus"] != "STOPPED":
            ecs.stop_task(
                cluster=os.environ["FARGATE_CLUSTER"],
                task=task_arn,
                reason="CloudRescue inference request finished or deadline reached",
            )
