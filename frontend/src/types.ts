export type Status =
  | 'ANALYZING'
  | 'WAITING_APPROVAL'
  | 'SIMULATING'
  | 'COMPLETED'
  | 'ESCALATED'
  | 'FAILED';
export type Playbook =
  | 'database_failover'
  | 'replace_unhealthy_compute'
  | 'scale_capacity'
  | 'investigate_network'
  | 'escalate_to_human';
export interface Scenario {
  id: string;
  name: string;
  description: string;
  service: string;
  severity: string;
}
export interface IncidentEvent {
  at: string;
  title: string;
  detail: string;
}
export interface Decision {
  choice: Playbook;
  confidence: number;
  probabilities: Record<string, number>;
  latency_ms: number;
  model_id: string;
  state_hash: string;
  source: string;
  runtime?: string;
  inference_ms?: number;
  model_load_ms?: number;
}
export interface Incident {
  id: string;
  scenario_id: string;
  status: Status;
  source: string;
  resource_arn?: string;
  created_at: string;
  revision: number;
  observations: Record<string, string | number | boolean | null>;
  events: IncidentEvent[];
  decision?: Decision;
  validation?: { allowed: boolean; reasons: string[] };
  explanation?: string;
  approval_digest?: string;
  approval_expires_at?: number;
  report?: string;
  report_source?: string;
}

export interface LiveInventory {
  source: string;
  account_id: string;
  region: string;
  collected_at: string | null;
  complete: boolean;
  truncated?: boolean;
  errors: string[];
  resources: Array<{
    id: string;
    arn: string;
    name: string;
    kind: string;
    state: string;
    region: string;
    metrics: Array<{
      name: string;
      value: number | null;
      timestamp: string | null;
      status: string;
      statistic: string;
      period_seconds: number;
    }>;
    alarms: Array<{ name: string; state: string; updated_at: string }>;
  }>;
}

export interface RecoveryConfiguration {
  enabled: boolean;
  can_approve: boolean;
  operator_id: string;
  policy: {
    account_id: string;
    primary_region: string;
    dr_region: string;
    rto_seconds: number;
    rpo_seconds: number;
    resource_types: string[];
    scope: string;
    approver_group: string;
    automatic_cutover: boolean;
  };
  targets: Array<{ id: string; resource_arn: string; resource_type: string; dr_region: string }>;
}

export interface RestorePlan {
  id: string;
  target_id: string;
  created_by: string;
  created_at: string;
  status: string;
  revision: number;
  dr_region: string;
  source_arn: string;
  confirmation: string;
  approval_digest: string;
  expires_at: number;
  metadata: Record<string, string>;
  recovery_point: { arn: string; created_at: string; backup_age_seconds_at_plan: number };
  events: IncidentEvent[];
  restore_job_id?: string;
  created_resource_arn?: string;
  infrastructure_restore_seconds?: number;
}
export interface RuntimeConfig {
  mode: 'demo' | 'aws';
  apiUrl?: string;
  region?: string;
  userPoolId?: string;
  clientId?: string;
  authDomain?: string;
}
export interface Benchmark {
  count: number;
  average_latency_ms: number | null;
  source: string;
  evaluation?: {
    dataset: string;
    completed_at: string;
    runtime: string;
    vcpus: number;
    memory_mib: number;
    case_count: number;
    correct: number;
    accuracy: number;
    warm_p50_ms: number;
    warm_p95_ms: number;
    model_load_ms: number;
    job_elapsed_ms: number;
    errors: number;
    unsafe_recommendations_allowed: number;
    notes: string;
    cases: Array<{
      id: string;
      expected: string;
      predicted?: string;
      confidence?: number;
      inference_ms?: number;
      correct: boolean;
      safety_allowed: boolean;
      error?: string;
    }>;
  } | null;
}
