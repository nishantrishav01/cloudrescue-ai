import json
import os
import signal
import time
import traceback
from datetime import datetime, timezone

import boto3
import torch
from botocore.config import Config
from handler import engine, handler


def main():
    signal.alarm(1800)
    s3 = boto3.client(
        "s3",
        config=Config(
            connect_timeout=5,
            read_timeout=30,
            retries={"mode": "adaptive", "total_max_attempts": 3},
        ),
    )
    bucket = os.environ["INFERENCE_BUCKET"]
    job_id = os.environ["JOB_ID"]
    with s3.get_object(Bucket=bucket, Key=f"requests/{job_id}.json")["Body"] as body:
        job = json.loads(body.read())
    output = {
        "job_id": job_id,
        "runtime": "ecs-fargate-cpu",
        "vcpus": 4,
        "memory_mib": 12288,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "results": [],
    }
    try:
        started = time.perf_counter()
        engine()
        torch.set_num_threads(4)
        output["model_load_ms"] = round((time.perf_counter() - started) * 1000, 2)
        for index, case in enumerate(job["cases"]):
            started = time.perf_counter()
            try:
                result = handler(case["request"], None)
                output["results"].append(
                    {
                        "id": case["id"],
                        "response": result,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                        "first_after_load": index == 0,
                    }
                )
                print(
                    json.dumps({"case_id": case["id"], "latency_ms": result["latency_ms"]}),
                    flush=True,
                )
            except Exception as issue:
                traceback.print_exc()
                output["results"].append(
                    {
                        "id": case["id"],
                        "error": str(issue),
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                    }
                )
    except Exception as issue:
        traceback.print_exc()
        output["error"] = str(issue)
    output["finished_at"] = datetime.now(timezone.utc).isoformat()
    s3.put_object(
        Bucket=bucket,
        Key=f"results/{job_id}.json",
        Body=json.dumps(output).encode(),
        ContentType="application/json",
    )
    if output.get("error"):
        raise RuntimeError(output["error"])


if __name__ == "__main__":
    main()
