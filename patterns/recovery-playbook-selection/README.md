# Recovery playbook selection

Pattern: `recovery_playbook_selection` · `#tool-selection` · `#strands-decider-2b`

The reusable selector is `backend/agent/selector.py`. It submits a typed choice question to a separately deployed, IAM-protected Decider Lambda. It returns the model's actual choice, confidence, full option distribution, invocation latency, checkpoint identifier, and a canonical evidence hash. No heuristic or generative model substitutes the choice when inference fails.

```python
from backend.agent.selector import select_playbook
from backend.common.scenarios import evidence_for
from backend.common.safety import safety_gate

evidence = evidence_for("db-outage")
decision = select_playbook(evidence)
validation = safety_gate(evidence, decision)
print(decision, validation)
```

Set `AWS_REGION` and `DECIDER_FUNCTION`, use IAM role credentials, install `backend/requirements.txt`, and run from the repository root. The invocation identity needs `lambda:InvokeFunction` on that function only. The function must contain the official Decider checkpoint, not an imitation classifier.

The fixed options are database failover, replacement of unhealthy compute, scaling capacity, network investigation, and human escalation. The question and option-specific criteria are defined in `build_question` and `PLAYBOOKS`. The Strands explanation agent receives the immutable result after the tool-selection boundary; it has no recovery tools or permission to replace the choice.

Confidence below 0.80, stale/missing evidence, unverified prerequisites, and contradictory evidence block approval. The 0.80 threshold is an uncalibrated starting value. This implementation immediately escalates medium-confidence decisions instead of inventing a second telemetry pass. Operators must explicitly approve a valid current revision before any virtual action.

Actual runtime results belong in a benchmark artifact; demo fixtures do not qualify as inference evidence.
