from pathlib import Path

from backend.common.safety import PLAYBOOKS
from evaluations.benchmark import load_cases, summarize


def test_labeled_dataset_is_complete_and_labels_never_enter_requests():
    _, cases = load_cases(Path("evaluations/labeled-incidents.json"))
    assert len(cases) == len({case["id"] for case in cases}) == 30
    assert {case["expected_choice"] for case in cases} == set(PLAYBOOKS)
    for case in cases:
        assert set(case["request"]) == {"state", "questions"}
        assert "expected_choice" not in case["request"]["state"]
        assert "expected_safety_allow" not in case["request"]["state"]


def test_summary_counts_errors_and_excludes_first_prediction_from_warm_timings():
    dataset, cases = load_cases(Path("evaluations/labeled-incidents.json"))
    cases = cases[:3]
    results = []
    for case, latency in zip(cases[:2], [1000, 100], strict=True):
        choice = case["expected_choice"]
        results.append(
            {
                "id": case["id"],
                "response": {
                    "answers": {
                        "recovery_playbook": {
                            "choice": choice,
                            "confidence": 0.95,
                            "probabilities": {
                                key: 0.96 if key == choice else 0.01 for key in PLAYBOOKS
                            },
                        }
                    },
                    "model": "fixture-model",
                    "latency_ms": latency,
                },
            }
        )
    results.append({"id": cases[2]["id"], "error": "inference failed"})
    summary = summarize(
        dataset,
        cases,
        {
            "results": results,
            "runtime": "test-fixture",
            "vcpus": 4,
            "memory_mib": 12288,
            "job_elapsed_ms": 2000,
        },
    )
    assert summary["accuracy"] == 2 / 3
    assert summary["errors"] == 1
    assert summary["warm_p50_ms"] == summary["warm_p95_ms"] == 100
    assert summary["cases"][2]["safety_allowed"] is False
