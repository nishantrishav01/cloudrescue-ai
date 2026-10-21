# Recovery playbook selection

Pattern: `recovery_playbook_selection` · **Policy classification** · `#policy` · `#strands-decider-2b`

## Decision and integration

Classify normalized incident evidence into one response category before the agent proceeds to approval or escalation. This is a policy-classification pattern, not a claim that Decider dispatches distinct recovery tools or performs production failover.

The reusable selector is [`select_playbook`](../../backend/agent/selector.py). [`build_question`](../../backend/agent/selector.py) sends a typed `choice` question named `recovery_playbook`: "Based only on verified incident symptoms and prerequisites, which recovery investigation or playbook should be selected next? Escalate when evidence is missing or conflicting." The incident observations become `state`; the option-specific criteria come from [`PLAYBOOKS`](../../backend/common/safety.py).

| Choice | Required interpretation |
| --- | --- |
| `database_failover` | Investigate virtual failover only with an unavailable primary, healthy standby, verified acceptable replication lag and healthy network. |
| `replace_unhealthy_compute` | Replace verified unhealthy virtual compute only when database and network are healthy. |
| `scale_capacity` | Scale a saturated virtual cluster only with healthy dependencies and verified capacity policy. |
| `investigate_network` | Investigate a verified simulated network change; do not roll back real infrastructure. |
| `escalate_to_human` | Escalate missing, stale or conflicting evidence, or unmet prerequisites. |

The actual wiring lives in [`backend/agent/handler.py`](../../backend/agent/handler.py): collect evidence → `select_playbook` → deterministic `safety_gate` → approval or escalation → Bedrock explanation. The choice affects persisted workflow state. Bedrock receives the immutable result and cannot replace it. The safety checks are ordinary application logic, not a second Decider-powered guardrails pattern.

The selector returns the actual choice, confidence, full option distribution, request and model latency, checkpoint identifier, runtime and canonical evidence hash. No heuristic or generative model substitutes a choice when inference fails.

## Reuse

Install `backend/requirements.txt` in a virtual environment and run from the repository root with authorized IAM role/session credentials. Never embed access keys in examples or application configuration.

```python
from backend.agent.selector import select_playbook
from backend.common.scenarios import evidence_for
from backend.common.safety import safety_gate

evidence = evidence_for("db-outage")
decision = select_playbook(evidence)
validation = safety_gate(evidence, decision)
print(decision, validation)
```

This example makes real model/AWS calls and can incur charges. Its evidence is synthetic; its decision is not a frontend fixture. No restoration is executed. The official checkpoint must already be built and deployed; setting environment variables does not deploy a model.

### Fargate runtime

The verified deployment uses on-demand CPU Fargate tasks, not a continuously running endpoint. Set these variables to your own reviewed deployment values:

- `AWS_DEFAULT_REGION` and `AWS_REGION`: your deployment region.
- `DECIDER_RUNTIME=fargate`.
- `INFERENCE_BUCKET`: private inference request/result bucket.
- `FARGATE_CLUSTER` and `FARGATE_TASK_DEFINITION`: the designated cluster and worker task definition.
- `FARGATE_SUBNETS`: comma-separated verified subnet IDs.
- `FARGATE_SECURITY_GROUP`: the dedicated worker security group.

[`backend/agent/fargate.py`](../../backend/agent/fargate.py) writes a request, starts the designated worker, polls its private result and stops the task after completion or timeout. The task definition must provide its own `INFERENCE_BUCKET` and worker/model configuration. The current adapter requires public subnets with outbound connectivity and assigns a public IP; it opens no inbound access.

The caller needs scoped S3 request-write/result-read permissions; ECS run, describe and stop permissions; task-creation tagging permissions; and `iam:PassRole` restricted to the designated task/execution roles. The worker needs its separate scoped S3, image-pull and logging permissions. Review the actual grants in [`infrastructure/lib/decider-fargate.ts`](../../infrastructure/lib/decider-fargate.ts); do not substitute administrator permissions. The container must contain the official, revision-pinned Decider checkpoint.

### Optional Lambda runtime

Where sufficient Lambda memory is available, set `DECIDER_RUNTIME=lambda`, `DECIDER_FUNCTION` to the isolated model function, and both region variables. The invocation identity needs `lambda:InvokeFunction` on that exact function. This is an alternative adapter, not the runtime used by the verified Fargate deployment.

## Safety and limitations

Confidence below 0.80, stale/missing evidence, unverified prerequisites, and contradictory evidence block approval. The 0.80 threshold is an uncalibrated starting value. This implementation immediately escalates medium-confidence decisions instead of inventing a second telemetry pass. Operators must explicitly approve a valid current revision before any virtual action.

The example gate deliberately rejects non-simulation evidence. Live-resource analysis is advisory and cannot use this gate to authorize AWS changes. Separate real restoration is disabled by default and requires its own reviewed manifest and approval flow.

Adapt the question, option criteria, evidence schema and deterministic checks together for a different domain. Evaluate on labeled cases before trusting confidence thresholds. Use [`evaluations/run_benchmark.py`](../../evaluations/run_benchmark.py) with your own authorized deployment to measure actual inference; reports include private deployment metadata and must remain local. Demo fixtures do not qualify as inference evidence.

This pattern is included under the repository's [MIT license](../../LICENSE). Model weights, dependencies and AWS services retain their own licenses and terms.
