import json

import boto3
from botocore.config import Config


def main():
    client = boto3.client(
        "bedrock-runtime",
        config=Config(
            connect_timeout=5,
            read_timeout=60,
            retries={"mode": "adaptive", "total_max_attempts": 2},
        ),
    )
    response = client.converse(
        modelId="apac.amazon.nova-lite-v1:0",
        messages=[
            {"role": "user", "content": [{"text": "Reply only: CloudRescue connectivity verified"}]}
        ],
        inferenceConfig={"maxTokens": 32, "temperature": 0},
    )
    print(
        json.dumps(
            {"content": response["output"]["message"]["content"], "usage": response["usage"]}
        )
    )


if __name__ == "__main__":
    main()
