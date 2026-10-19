from decimal import Decimal

from boto3.dynamodb.types import TypeSerializer

from backend.common import store
from backend.common.safety import canonical_hash


def test_conditional_revision_and_nested_item_use_dynamodb_numbers(monkeypatch):
    captured = {}

    class FakeTable:
        def put_item(self, **arguments):
            serializer = TypeSerializer()
            serializer.serialize(arguments["Item"])
            serializer.serialize(arguments["ExpressionAttributeValues"])
            captured.update(arguments)

    monkeypatch.setattr(store, "table", lambda: FakeTable())
    store.save({"id": "sample", "revision": 2.0, "decision": {"confidence": 0.95}}, 1.0)
    assert captured["ExpressionAttributeValues"][":revision"] == Decimal("1.0")
    assert captured["Item"]["decision"]["confidence"] == Decimal("0.95")


def test_evidence_hash_survives_dynamodb_numeric_roundtrip():
    assert canonical_hash({"cpu": 98, "confidence": 1.0, "values": [0, 1.0]}) == canonical_hash(
        {"cpu": 98.0, "confidence": 1, "values": [0.0, 1]}
    )
