import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

PRIVATE_PREFIXES = (
    ".local/",
    ".aws/",
    ".codex/",
    ".agents/",
    "docs/",
    "artifacts/",
    "evaluations/results/",
    "test-results/",
    "playwright-report/",
)
PRIVATE_FILES = {
    "PLAN.md",
    "infrastructure/cdk.context.json",
    "infrastructure/recovery-targets.json",
    "infrastructure/outputs.json",
    "backend/lambda.zip",
}
SECRET_PATTERNS = {
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
    "literal-aws-secret": re.compile(
        r"""(?i)(?:aws_secret_access_key|aws_session_token)\s*[:=]\s*["'][A-Za-z0-9/+=]{40,}["']"""
    ),
}


def audit(paths: list[str], read_content, denied_values: list[str]) -> list[dict]:
    findings = []
    for filename in paths:
        path = PurePosixPath(filename)
        if (
            filename.startswith(PRIVATE_PREFIXES)
            or filename in PRIVATE_FILES
            or any(
                part in {"node_modules", ".venv", "__pycache__", ".package", "cdk.out", "dist"}
                for part in path.parts
            )
            or (path.name.startswith(".env") and path.name != ".env.example")
            or path.suffix.lower()
            in {".pem", ".key", ".p12", ".pfx", ".zip", ".db", ".sqlite", ".log"}
        ):
            findings.append({"path": filename, "rule": "private-or-generated-path"})
        content = read_content(filename)
        if b"\x00" in content:
            findings.append({"path": filename, "rule": "unexpected-binary-file"})
            continue
        text = content.decode("utf-8-sig", errors="replace")
        for rule, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append({"path": filename, "rule": rule})
        if any(value and value in text for value in denied_values):
            findings.append({"path": filename, "rule": "private-deployment-identifier"})
    return findings


def main():
    parser = argparse.ArgumentParser(
        description="Audit the Git index without printing matched secrets."
    )
    parser.add_argument("--denylist", default=".local/publication-denylist.json")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    tracked = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=root).decode()
    paths = []
    for entry in tracked.split("\0"):
        if not entry:
            continue
        metadata, filename = entry.split("\t", 1)
        if metadata.split()[0] == "120000":
            print(
                json.dumps({"status": "blocked", "path": filename, "rule": "symlink-not-reviewed"})
            )
            return 1
        paths.append(filename)
    if not paths:
        print(
            json.dumps({"status": "blocked", "reason": "No staged/tracked source files to audit."})
        )
        return 1
    denylist = root / arguments.denylist
    values = json.loads(denylist.read_text(encoding="utf-8")) if denylist.exists() else []
    findings = audit(
        paths,
        lambda filename: subprocess.check_output(["git", "show", f":{filename}"], cwd=root),
        values,
    )
    print(
        json.dumps(
            {
                "status": "passed" if not findings else "blocked",
                "files_checked": len(paths),
                "findings": findings,
            }
        )
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
