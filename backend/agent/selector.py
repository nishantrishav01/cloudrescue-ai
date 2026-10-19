import json
import os
import time
from functools import lru_cache

import boto3
from botocore.config import Config

from backend.common.safety import PLAYBOOKS, canonical_hash, validate_decision


class InferenceUnavailable(Exception):
    pass


def build_question(observations: dict) -> dict:
    return {
        "state": json.dumps(observations, sort_keys=True),
        "questions": {
            "recovery_playbook": {
                "type": "choice",
                "instructions": "Based only on verified incident symptoms and prerequisites, which recovery investigation or playbook should be selected next? Escalate when evidence is missing or conflicting.",
                "criteria": PLAYBOOKS,
            }
        },
    }


@lru_cache
def lambda_client():
    return boto3.client(
        "lambda",
        config=Config(
            connect_timeout=5,
            read_timeout=850,
            retries={"total_max_attempts": 1, "mode": "standard"},
        ),
    )


def select_playbook(observations: dict) -> dict:
    function_name = os.environ.get("DECIDER_FUNCTION")
    runtime = os.environ.get("DECIDER_RUNTIME", "lambda")
    if not function_name and runtime != "fargate":
        raise InferenceUnavailable(
            "The isolated Decider inference container has not been configured."
        )
    started = time.perf_counter()
    model_load_ms = None
    if runtime == "fargate":
        from backend.agent.fargate import run_job

        job = run_job([{"id": "incident", "request": build_question(observations)}])
        if not job.get("results") or "response" not in job["results"][0]:
            raise InferenceUnavailable("Decider worker did not return a valid inference result.")
        payload = job["results"][0]["response"]
        model_load_ms = job.get("model_load_ms")
    else:
        response = lambda_client().invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(build_question(observations)).encode(),
        )
        with response["Payload"] as body:
            payload = json.loads(body.read())
        if response.get("FunctionError"):
            raise InferenceUnavailable("Decider inference failed. No recovery was selected.")
    try:
        answer = payload["answers"]["recovery_playbook"]
        decision = {
            "choice": answer["choice"],
            "confidence": answer["confidence"],
            "probabilities": answer["probabilities"],
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "model_id": payload["model"],
            "state_hash": canonical_hash(observations),
            "source": "strands-decider",
            "runtime": runtime,
            "inference_ms": payload.get("latency_ms"),
            "model_load_ms": model_load_ms,
        }
        return validate_decision(decision)
    except (KeyError, TypeError, ValueError) as issue:
        raise InferenceUnavailable(
            "Decider returned an invalid response. Recovery is blocked."
        ) from issue
