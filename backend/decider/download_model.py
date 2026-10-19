import json
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

MODEL_ID = "StrandsAgents/strands-decider-2B-qwen3.5-v1-2610"


def main():
    revision = HfApi().model_info(MODEL_ID).sha
    checkpoint = Path(snapshot_download(MODEL_ID, revision=revision))
    config = json.loads((checkpoint / "strands_decider_config.json").read_text())
    provenance_file = checkpoint / "provenance.json"
    provenance = json.loads(provenance_file.read_text()) if provenance_file.exists() else {}
    base_revision = config.get("base_revision") or provenance.get("base_model_revision")
    if not base_revision:
        raise RuntimeError("Checkpoint must specify an immutable base model revision.")
    snapshot_download(
        config["base_model"],
        revision=base_revision,
        allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt", "*.jinja"],
    )
    Path("/opt/model-manifest.json").write_text(
        json.dumps(
            {
                "model_id": MODEL_ID,
                "revision": revision,
                "checkpoint": str(checkpoint),
                "base_model": config["base_model"],
                "base_revision": base_revision,
            }
        )
    )


if __name__ == "__main__":
    main()
