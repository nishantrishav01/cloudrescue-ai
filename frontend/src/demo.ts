import type { Incident, Playbook, Scenario } from './types';

export const scenarios: Scenario[] = [
  {
    id: 'db-outage',
    name: 'Database outage',
    description: 'Primary database unavailable. A healthy standby is ready.',
    service: 'Amazon RDS',
    severity: 'Critical',
  },
  {
    id: 'compute-overload',
    name: 'Compute overload',
    description: 'A traffic surge is pushing compute capacity to its limit.',
    service: 'Amazon EC2',
    severity: 'High',
  },
  {
    id: 'network-failure',
    name: 'Network disruption',
    description: 'Investigate failed connectivity after a configuration change.',
    service: 'Amazon VPC',
    severity: 'High',
  },
  {
    id: 'missing-standby',
    name: 'Missing evidence',
    description: 'Database unavailable, but standby health is unknown.',
    service: 'Amazon RDS',
    severity: 'Critical',
  },
  {
    id: 'conflicting-symptoms',
    name: 'Conflicting signals',
    description: 'Overlapping symptoms require an operator investigation.',
    service: 'Multiple services',
    severity: 'High',
  },
];
const decisions: Record<string, Playbook> = {
  'db-outage': 'database_failover',
  'compute-overload': 'scale_capacity',
  'network-failure': 'investigate_network',
  'missing-standby': 'escalate_to_human',
  'conflicting-symptoms': 'escalate_to_human',
};
const incidents = new Map<string, Incident>();
const event = (title: string, detail: string) => ({ at: new Date().toISOString(), title, detail });

export function createDemo(scenarioId: string): Incident {
  const incident: Incident = {
    id: crypto.randomUUID(),
    scenario_id: scenarioId,
    status: 'ANALYZING',
    source: 'demo-fixture',
    created_at: new Date().toISOString(),
    revision: 1,
    observations: {
      primary_db_health:
        scenarioId.includes('db') || scenarioId === 'missing-standby' ? 'unavailable' : 'healthy',
      standby_db_health: scenarioId === 'missing-standby' ? 'unknown' : 'healthy',
      cpu_percent: scenarioId === 'compute-overload' ? 98 : 34,
      connection_error_percent: scenarioId === 'db-outage' ? 88 : 2,
      network_health: scenarioId === 'network-failure' ? 'degraded' : 'healthy',
      evidence_source: 'Illustrative scenario fixture',
    },
    events: [
      event(
        'Incident detected',
        'A virtual incident was created in the isolated demo environment.',
      ),
    ],
  };
  incidents.set(incident.id, incident);
  return structuredClone(incident);
}

export function readDemo(id: string): Incident {
  const incident = incidents.get(id);
  if (!incident) throw new Error('Demo incident is no longer available. Start another scenario.');
  const elapsed = Date.now() - Date.parse(incident.created_at);
  if (incident.status === 'ANALYZING' && elapsed > 2200) {
    const choice = decisions[incident.scenario_id];
    const ambiguous = choice === 'escalate_to_human';
    incident.decision = {
      choice,
      confidence: ambiguous ? 0.46 : 0.94,
      probabilities: Object.fromEntries(
        (
          [
            'database_failover',
            'replace_unhealthy_compute',
            'scale_capacity',
            'investigate_network',
            'escalate_to_human',
          ] as Playbook[]
        ).map((item) => [
          item,
          item === choice ? (ambiguous ? 0.568 : 0.952) : ambiguous ? 0.108 : 0.012,
        ]),
      ),
      latency_ms: 0,
      model_id: 'Illustrative demo fixture · no inference',
      source: 'demo-fixture',
      state_hash: 'demo',
    };
    incident.validation = {
      allowed: !ambiguous,
      reasons: [
        ambiguous
          ? 'Evidence is incomplete or conflicting. Human investigation required.'
          : 'Scenario prerequisites verified. Only a virtual recovery can be approved.',
      ],
    };
    incident.status = ambiguous ? 'ESCALATED' : 'WAITING_APPROVAL';
    incident.approval_digest = 'demo';
    incident.explanation = ambiguous
      ? 'There is not enough consistent evidence to recommend a recovery. Gather additional telemetry and verify prerequisites before proceeding.'
      : `The sample evidence supports ${choice.replaceAll('_', ' ')}. Review the evidence and approve the virtual playbook to explore the recovery sequence.`;
    incident.events.push(
      event('Evidence collected', 'Virtual service health and incident signals are available.'),
      event(
        'Sample decision ready',
        'Illustrative confidence only. Decider and Bedrock were not called.',
      ),
      event(
        ambiguous ? 'Escalated to operator' : 'Safety checks passed',
        incident.validation.reasons[0],
      ),
    );
  }
  if (incident.status === 'SIMULATING') {
    const approval = incident.events.find((item) => item.title === 'Operator approved');
    if (approval && Date.now() - Date.parse(approval.at) > 2800) {
      incident.status = 'COMPLETED';
      incident.events.push(
        event(
          'Virtual playbook executed',
          'Applied the selected action to the simulated resource state.',
        ),
        event(
          'Recovery verified',
          'Virtual health checks passed. No AWS infrastructure was changed.',
        ),
      );
      incident.report_source = 'demo-fixture';
      incident.report = `# CloudRescue AI · Demo incident report\n\nIncident: ${incident.id}\nScenario: ${incident.scenario_id}\nPlaybook: ${incident.decision?.choice}\n\nThis was a deterministic frontend demonstration. No models were invoked and no real infrastructure was changed.\n\n${incident.events.map((item) => `- ${item.at}: ${item.title} — ${item.detail}`).join('\n')}\n\nOutcome: virtual service health restored. Recommended follow-up: review monitoring coverage and rehearse the recovery procedure.`;
    }
  }
  return structuredClone(incident);
}

export function approveDemo(id: string): Incident {
  const incident = incidents.get(id);
  if (!incident || incident.status !== 'WAITING_APPROVAL')
    throw new Error('This incident is not awaiting approval.');
  incident.status = 'SIMULATING';
  incident.events.push(
    event('Operator approved', 'Explicit approval granted for one simulated playbook.'),
  );
  return structuredClone(incident);
}
