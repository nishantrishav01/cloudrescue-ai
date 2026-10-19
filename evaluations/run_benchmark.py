import argparse
import json
import os
from pathlib import Path

import boto3

from backend.agent.fargate import run_job
from evaluations.benchmark import load_cases, summarize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", default="infrastructure/outputs.json")
    parser.add_argument("--dataset", default="evaluations/labeled-incidents.json")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--publish", action="store_true")
    arguments = parser.parse_args()
    outputs = json.loads(Path(arguments.outputs).read_text(encoding="utf-8"))
    config = json.loads(
        next(
            value["DeciderRuntimeConfig"]
            for value in outputs.values()
            if "DeciderRuntimeConfig" in value
        )
    )
    os.environ.update(config)
    region = config["FARGATE_CLUSTER"].split(":")[3]
    os.environ["AWS_DEFAULT_REGION"] = region
    account = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    if account != config["FARGATE_CLUSTER"].split(":")[4]:
        raise RuntimeError("Credentials do not match the inference deployment account.")
    dataset, cases = load_cases(Path(arguments.dataset))
    if arguments.limit:
        cases = cases[: arguments.limit]
    if not cases:
        raise ValueError("No labeled cases selected.")
    print(
        json.dumps(
            {
                "status": "starting-real-inference",
                "cases": len(cases),
                "runtime": "ecs-fargate-cpu",
                "dataset_hash_pending_results": True,
            }
        ),
        flush=True,
    )
    job = run_job(
        [{"id": case["id"], "request": case["request"]} for case in cases], timeout_seconds=1800
    )
    report = summarize(dataset, cases, job)
    report.update(
        account_id=account,
        region=region,
        task_arn=job["task_arn"],
        image_uri=json.loads(Path("infrastructure/cdk.context.json").read_text())[
            "deciderImageUri"
        ],
    )
    destination = Path("evaluations/results")
    destination.mkdir(exist_ok=True)
    report_path = destination / f"benchmark-{job['job_id']}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [
        "# CloudRescue AI measured inference benchmark",
        "",
        f"Dataset: {report['dataset']}",
        f"Dataset hash: {report['dataset_hash']}",
        f"Account/region: {account} / {region}",
        "Runtime: ECS Fargate CPU, 4 vCPUs, 12 GiB RAM",
        f"Model image: {report['image_uri']}",
        "",
        f"Accuracy: {report['correct']}/{report['case_count']} ({report['accuracy']:.1%})",
        f"Warm latency p50/p95: {report['warm_p50_ms']} / {report['warm_p95_ms']} ms",
        f"Model loading: {report['model_load_ms']} ms",
        f"Task/request elapsed: {report['job_elapsed_ms']} ms",
        f"Errors: {report['errors']}",
        f"Unsafe recommendations allowed by policy: {report['unsafe_recommendations_allowed']}",
        "",
        report["notes"],
        "",
        "| Case | Expected | Actual | Confidence | Inference ms | Safety permits approval |",
        "|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {row['id']} | {row['expected']} | {row.get('predicted', 'ERROR')} | {row.get('confidence', 'n/a')} | {row.get('inference_ms', 'n/a')} | {row['safety_allowed']} |"
        for row in report["cases"]
    )
    report_path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if arguments.publish:
        if arguments.limit or report["errors"] or report["valid_inferences"] != 30:
            raise RuntimeError(
                "Only a complete thirty-case run without inference errors may be published."
            )
        boto3.client("s3", region_name=region).put_object(
            Bucket=config["INFERENCE_BUCKET"],
            Key="benchmarks/latest.json",
            Body=json.dumps(report).encode(),
            ContentType="application/json",
        )
    print(
        json.dumps(
            {
                key: report[key]
                for key in [
                    "case_count",
                    "correct",
                    "accuracy",
                    "warm_p50_ms",
                    "warm_p95_ms",
                    "model_load_ms",
                    "errors",
                    "unsafe_recommendations_allowed",
                ]
            }
            | {"report": str(report_path), "published": arguments.publish}
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
