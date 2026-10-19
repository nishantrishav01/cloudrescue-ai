import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.agent.selector import build_question
from backend.common.safety import PLAYBOOKS, canonical_hash, safety_gate, validate_decision


def load_cases(path: Path) -> tuple[dict, list[dict]]:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    now = datetime.now(timezone.utc)
    seen = set()
    for item in dataset["cases"]:
        if item["id"] in seen or item["expected_choice"] not in PLAYBOOKS:
            raise ValueError(
                "Dataset case IDs must be unique and labels must be allowed playbooks."
            )
        seen.add(item["id"])
        age = item.get("evidence_age_seconds", 0)
        observations = {
            **dataset["profiles"][item["profile"]],
            **item["evidence"],
            "evidence_source": "simulation",
            "observed_at": (now - timedelta(seconds=age)).isoformat(),
            "last_evidence_age_seconds": age,
        }
        cases.append(
            {
                **item,
                "observations": observations,
                "evaluation_time": now.isoformat(),
                "request": build_question(observations),
            }
        )
    return dataset, cases


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 2)


def summarize(dataset: dict, cases: list[dict], job: dict) -> dict:
    returned = {item["id"]: item for item in job["results"]}
    rows = []
    matrix = {expected: {predicted: 0 for predicted in PLAYBOOKS} for expected in PLAYBOOKS}
    for case in cases:
        result = returned.get(case["id"], {})
        row = {
            "id": case["id"],
            "expected": case["expected_choice"],
            "reason": case["reason"],
            "expected_safety_allow": case["expected_safety_allow"],
        }
        try:
            payload = result["response"]
            answer = validate_decision(payload["answers"]["recovery_playbook"])
            validation = safety_gate(
                case["observations"], answer, now=datetime.fromisoformat(case["evaluation_time"])
            )
            row.update(
                predicted=answer["choice"],
                confidence=answer["confidence"],
                correct=answer["choice"] == case["expected_choice"],
                inference_ms=payload["latency_ms"],
                model_id=payload["model"],
                safety_allowed=validation["allowed"],
                safety_reasons=validation["reasons"],
                probabilities=answer["probabilities"],
            )
            matrix[case["expected_choice"]][answer["choice"]] += 1
        except (KeyError, TypeError, ValueError) as issue:
            row.update(correct=False, error=result.get("error", str(issue)), safety_allowed=False)
        rows.append(row)
    valid = [row for row in rows if "error" not in row]
    latencies = [row["inference_ms"] for row in valid]
    warm_latencies = [row["inference_ms"] for row in valid if row["id"] != cases[0]["id"]]
    return {
        "dataset": dataset["name"],
        "dataset_hash": canonical_hash(dataset),
        "completed_at": job.get("finished_at"),
        "runtime": job["runtime"],
        "vcpus": job["vcpus"],
        "memory_mib": job["memory_mib"],
        "case_count": len(rows),
        "valid_inferences": len(valid),
        "correct": sum(row["correct"] for row in rows),
        "accuracy": sum(row["correct"] for row in rows) / len(rows),
        "errors": len(rows) - len(valid),
        "median_inference_ms": percentile(latencies, 0.5),
        "warm_p50_ms": percentile(warm_latencies, 0.5),
        "warm_p95_ms": percentile(warm_latencies, 0.95),
        "model_load_ms": job.get("model_load_ms"),
        "job_elapsed_ms": job["job_elapsed_ms"],
        "unsafe_recommendations_allowed": sum(
            row["safety_allowed"]
            and (not row["expected_safety_allow"] or row.get("predicted") != row["expected"])
            for row in rows
        ),
        "policy_approval_count": sum(row["safety_allowed"] for row in rows),
        "escalation_rate": sum(not row["safety_allowed"] for row in rows) / len(rows),
        "confusion_matrix": matrix,
        "cases": rows,
        "notes": "Synthetic diagnostic cases, not production accuracy. CPU Fargate timings, not Lambda cold starts. Model load is measured separately. First prediction excluded from warm percentiles. No recovery actions executed. The confidence threshold remains uncalibrated.",
    }
