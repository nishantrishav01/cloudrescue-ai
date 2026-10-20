# Security and publication boundary

This repository contains application source, tests and synthetic example incidents. It must not contain credentials, customer telemetry or environment-specific deployment records.

## Local-only material

The following paths are deliberately ignored:

- `.local/`, including the detailed application/deployment document and publication audit reports.
- `docs/` and `PLAN.md`, which contain local operational documentation.
- `infrastructure/cdk.context.json`, `infrastructure/recovery-targets.json` and `infrastructure/outputs.json`.
- Generated CDK assemblies, Lambda packages, frontend builds, screenshots, smoke-test artifacts and measured evaluation reports.
- AWS/tool configuration directories, environment files, private keys, certificates/keystores and database dumps matched by `.gitignore`.

Use the committed `.example.json` configuration files as templates. The example account ID is fictitious. Never place credentials in those files. Obtain AWS credentials through authorized CLI sessions and runtime IAM roles; store GitHub authentication in the operating system credential store.

The public README screenshot at `assets/cloudrescue-war-room.png` is a visually reviewed demo, not a live deployment capture. The publication audit allows only its reviewed SHA-256 digest. Replacing it requires a fresh visual privacy review and an updated digest; secret scanners cannot detect sensitive information displayed inside an image.

## Before every push

1. Review `git diff --cached` and `git diff --cached --name-only`; ignored files can still be force-added, so never rely on `.gitignore` alone.
2. Run `python scripts/check_publication.py` to check staged paths, known secret patterns and any private local deployment denylist.
3. Run an independent secrets scanner against the staged content and complete Git history, with redacted output.
4. Run the applicable backend, infrastructure, browser, type and lint checks.

No automated scanner can guarantee that arbitrary sensitive data is absent. If a credential is ever published, revoke/rotate it immediately and follow the hosting provider's history-removal process. Deleting the visible file is not sufficient.

Do not put vulnerabilities, tokens, account inventory or logs into public issues. Use GitHub private vulnerability reporting when available, or arrange a private channel with the repository owner.

## Runtime limitations

Live AI investigation is read-only. Synthetic recovery is separate from real AWS restoration. Real restore execution is disabled by default and requires an explicit target manifest, scoped service roles, current evidence, a different authenticated approver, an exclusive target lock and acknowledgement of AWS charges. The project is not a production-ready autonomous disaster-recovery system; application validation, regional management-plane resilience, replication, cutover/failback and production security controls require independent validation.
