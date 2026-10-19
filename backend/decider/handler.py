import json
import time
from functools import lru_cache
from pathlib import Path

import torch
from strands_decider.infer import EngineConfig, SystemOneEngine
from strands_decider.modeling import StrandsDeciderModel
from strands_decider.schema import SystemOneRequest


@lru_cache
def engine():
    manifest = json.loads(Path("/opt/model-manifest.json").read_text())
    torch.set_num_threads(6)
    model = StrandsDeciderModel.load(manifest["checkpoint"], attn_implementation="eager")
    return SystemOneEngine(
        model,
        EngineConfig(
            device="cpu",
            use_prefix_cache=False,
            max_batch=1,
            model_name=f"{manifest['model_id']}@{manifest['revision']}",
        ),
    )


def handler(event, context):
    started = time.perf_counter()
    inference_engine = engine()
    with torch.inference_mode():
        response = inference_engine.evaluate(SystemOneRequest.model_validate(event)).model_dump()
    response["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return response
