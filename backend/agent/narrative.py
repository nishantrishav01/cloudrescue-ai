import json
import os
import time

from botocore.config import Config
from strands import Agent
from strands.models import BedrockModel


def explain(incident: dict, report: bool = False) -> tuple[str, float]:
    model = BedrockModel(
        model_id=os.environ["BEDROCK_MODEL_ID"],
        region_name=os.environ["AWS_REGION"],
        max_tokens=650 if report else 250,
        temperature=0.2,
        streaming=False,
        boto_client_config=Config(
            connect_timeout=5,
            read_timeout=60,
            retries={"mode": "adaptive", "total_max_attempts": 3},
        ),
    )
    agent = Agent(
        model=model,
        tools=[],
        callback_handler=None,
        system_prompt="You are CloudRescue's incident explanation agent. Use only the supplied evidence, immutable Decider choice, safety result and recorded outcome. Never choose a different playbook or claim actual AWS remediation. Distinguish simulated evidence from read-only AWS telemetry. Do not invent measurements. Be concise and clear.",
    )
    data = {
        key: incident.get(key)
        for key in (
            "id",
            "scenario_id",
            "observations",
            "decision",
            "validation",
            "events",
            "status",
            "telemetry",
        )
    }
    started = time.perf_counter()
    result = agent(
        (
            "Write a Markdown incident report covering symptoms, model choice, safety/approval, actual simulated outcome and prevention."
            if report
            else "Explain the recorded recommendation and its limitations in at most three sentences."
        )
        + "\nEvidence:\n"
        + json.dumps(data, sort_keys=True)
    )
    return str(result), round((time.perf_counter() - started) * 1000, 2)
