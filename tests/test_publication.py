from scripts.check_publication import audit


def test_public_source_and_synthetic_examples_are_allowed():
    assert (
        audit(
            ["backend/api/main.py", "infrastructure/recovery-targets.example.json"],
            lambda path: b'{"account_id":"111122223333"}',
            ["private-deployment-marker"],
        )
        == []
    )


def test_private_files_are_rejected_without_printing_values():
    for filename in [
        ".local/private.md",
        "docs/operations.md",
        "infrastructure/cdk.context.json",
        "infrastructure/recovery-targets.json",
        "infrastructure/outputs.json",
        "evaluations/results/run.json",
        ".env.production",
        "private.pem",
        "frontend/dist/index.html",
    ]:
        findings = audit([filename], lambda path: b"ordinary content", [])
        assert findings and findings[0]["rule"] == "private-or-generated-path"


def test_deployment_identifiers_and_private_keys_are_rejected():
    private_marker = "example-private-deployment-marker"
    findings = audit(["README.md"], lambda path: private_marker.encode(), [private_marker])
    assert findings == [{"path": "README.md", "rule": "private-deployment-identifier"}]
    findings = audit(["source.txt"], lambda path: b"-----BEGIN " + b"PRIVATE KEY-----", [])
    assert findings == [{"path": "source.txt", "rule": "private-key"}]


def test_unreviewed_binary_content_is_rejected():
    assert (
        audit(["unknown.bin"], lambda path: b"\x00\x01", [])[0]["rule"] == "unexpected-binary-file"
    )
