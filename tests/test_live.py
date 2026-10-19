import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.live import discovery


def test_metrics_preserve_missing_and_stale_measurements(monkeypatch):
    now = datetime.now(timezone.utc)

    def pages(**arguments):
        assert arguments["ScanBy"] == "TimestampDescending"
        yield {
            "MetricDataResults": [
                {
                    "Id": "metric0",
                    "Values": [42.5],
                    "Timestamps": [now - timedelta(minutes=30)],
                    "StatusCode": "Complete",
                },
                {"Id": "metric1", "Values": [], "Timestamps": [], "StatusCode": "Complete"},
            ]
        }

    monkeypatch.setattr(
        discovery,
        "client",
        lambda *args: SimpleNamespace(
            get_paginator=lambda operation: SimpleNamespace(paginate=pages)
        ),
    )
    resources = [{"id": "i-1234", "kind": "EC2", "metrics": []}]
    discovery.add_metrics(resources)
    metrics = resources[0]["metrics"]
    assert metrics[0]["value"] == 42.5
    assert metrics[0]["status"] == "stale-or-partial"
    assert metrics[1]["value"] is None and metrics[1]["status"] == "missing"


def test_partial_discovery_never_reports_complete(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")

    def pages(**arguments):
        raise RuntimeError("service unavailable")

    monkeypatch.setattr(
        discovery,
        "client",
        lambda *args: SimpleNamespace(
            get_paginator=lambda operation: SimpleNamespace(paginate=pages)
        ),
    )
    result = discovery.collect()
    assert result["complete"] is False
    assert len(result["errors"]) >= 4
    assert result["resources"] == []


def test_live_investigation_rejects_stale_or_unlisted_resource(monkeypatch):
    data = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "resources": [],
        "account_id": "123456789012",
        "region": "ap-south-1",
    }
    monkeypatch.setattr(discovery, "snapshot", lambda: copy.deepcopy(data))
    with pytest.raises(ValueError, match="not in"):
        discovery.evidence_for_resource("arn:aws:ec2:ap-south-1:123456789012:instance/i-1234")
    data["collected_at"] = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    with pytest.raises(ValueError, match="Refresh"):
        discovery.evidence_for_resource("anything")
