import { useEffect, useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import { api, config } from '../api';
import type { RecoveryConfiguration, RestorePlan } from '../types';

export function DisasterRecovery({ signedIn }: { signedIn: boolean }) {
  const [configuration, setConfiguration] = useState<RecoveryConfiguration | null>(null);
  const [plans, setPlans] = useState<RestorePlan[]>([]);
  const [target, setTarget] = useState('');
  const [confirmation, setConfirmation] = useState<Record<string, string>>({});
  const [costsAccepted, setCostsAccepted] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const connected = config.mode === 'aws' && signedIn;
  useEffect(() => {
    if (!connected) return;
    let cancelled = false;
    const load = () =>
      void Promise.all([api.recoveryConfiguration(), api.restorePlans()])
        .then(([settings, records]) => {
          if (!cancelled) {
            setConfiguration(settings);
            setPlans(records);
          }
        })
        .catch((issue: Error) => {
          if (!cancelled) setError(issue.message);
        });
    load();
    const timer = window.setInterval(load, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [connected]);
  async function run(action: () => Promise<RestorePlan>) {
    setBusy(true);
    setError('');
    try {
      const plan = await action();
      setPlans((previous) => [plan, ...previous.filter((item) => item.id !== plan.id)]);
    } catch (issue) {
      setError((issue as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const policy = configuration?.policy;
  return (
    <div className="live-layout">
      <section className="panel">
        <div className="panel-heading">
          <h2>
            <ShieldCheck size={17} /> Approved recovery scope
          </h2>
          <span className="tiny-label">
            {configuration?.enabled ? 'REAL RESTORES ENABLED' : 'REAL RESTORES BLOCKED'}
          </span>
        </div>
        <div className="live-content">
          {!connected && (
            <p>Sign in to view the deployed recovery policy. No demo restore can execute here.</p>
          )}
          {error && (
            <p className="error-banner" role="alert">
              {error}
            </p>
          )}
          {policy && (
            <>
              <div className="recovery-policy">
                <div>
                  <span>Account</span>
                  <strong>{policy.account_id}</strong>
                </div>
                <div>
                  <span>Primary → DR</span>
                  <strong>
                    {policy.primary_region} → {policy.dr_region}
                  </strong>
                </div>
                <div>
                  <span>RTO / RPO objectives</span>
                  <strong>
                    {policy.rto_seconds / 3600} hour / {policy.rpo_seconds / 60} minutes
                  </strong>
                </div>
                <div>
                  <span>Considered resource types</span>
                  <strong>{policy.resource_types.join(', ')}</strong>
                </div>
              </div>
              <p>
                Scope: all discovered resources in this account. Discovery is not proof of DR
                protection. Reviewed restore targets:{' '}
                <strong>{configuration.targets.length}</strong>.
              </p>
              <div className="safety-message blocked">
                Two-person approval is mandatory: a different operator in {policy.approver_group}{' '}
                must approve the exact plan within 10 minutes. Approval permits billable
                infrastructure restoration only. DNS cutover and source shutdown are not included.
              </div>
              {!configuration.enabled && (
                <p>
                  Real restore permissions are disabled. First configure reviewed Singapore backup
                  copies, private networking, a restricted AWS Backup restore role and target
                  metadata in <code>infrastructure/recovery-targets.json</code>, then deploy through
                  CDK.
                </p>
              )}
              <p>
                Current executable runbook: EC2/RDS restoration from an encrypted AWS Backup
                recovery point no older than 15 minutes. Lambda code/configuration must be deployed
                to Singapore using its workload's IaC pipeline. Neither snapshot age nor a completed
                restore proves actual application RPO/RTO.
              </p>
              <div className="live-controls">
                <label>
                  Reviewed target
                  <select
                    aria-label="Reviewed recovery target"
                    value={target}
                    onChange={(event) => setTarget(event.target.value)}
                  >
                    <option value="">Choose a configured target</option>
                    {configuration.targets.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.id} · {item.resource_type}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="button secondary"
                  disabled={!target || busy}
                  onClick={() => void run(() => api.createRestore(target))}
                >
                  Prepare immutable restore plan
                </button>
              </div>
            </>
          )}
        </div>
      </section>
      {plans.map((plan) => (
        <section className="panel" key={plan.id}>
          <div className="panel-heading">
            <h2>{plan.target_id}</h2>
            <span className="tiny-label">{plan.status.replaceAll('_', ' ')}</span>
          </div>
          <div className="live-content">
            <code>{plan.id}</code>
            <p>Source: {plan.source_arn}</p>
            <p>
              Destination: {plan.dr_region} · Recovery point: {plan.recovery_point.created_at} ·
              Backup age at planning: {plan.recovery_point.backup_age_seconds_at_plan}s
            </p>
            <details>
              <summary>Review exact AWS restore metadata and recovery point</summary>
              <pre>
                {JSON.stringify(
                  { recovery_point: plan.recovery_point, metadata: plan.metadata },
                  null,
                  2,
                )}
              </pre>
            </details>
            {plan.restore_job_id && (
              <p>
                AWS restore job: <code>{plan.restore_job_id}</code>
              </p>
            )}
            {plan.created_resource_arn && (
              <p>
                Restored resource: <code>{plan.created_resource_arn}</code>
              </p>
            )}
            {plan.status === 'WAITING_APPROVAL' && (
              <div className="restore-approval">
                <p>
                  Created by {plan.created_by}.{' '}
                  {configuration?.operator_id === plan.created_by
                    ? 'You cannot approve your own plan.'
                    : !configuration?.can_approve
                      ? 'An independent RecoveryApprovers operator must approve.'
                      : 'Review the plan and confirm the destination.'}
                </p>
                <label>
                  Type exactly: <code>{plan.confirmation}</code>
                  <input
                    aria-label={`Confirmation for ${plan.target_id}`}
                    value={confirmation[plan.id] ?? ''}
                    onChange={(event) =>
                      setConfirmation((previous) => ({
                        ...previous,
                        [plan.id]: event.target.value,
                      }))
                    }
                    autoComplete="off"
                  />
                </label>
                <label className="cost-acknowledgement">
                  <input
                    type="checkbox"
                    checked={costsAccepted[plan.id] ?? false}
                    onChange={(event) =>
                      setCostsAccepted((previous) => ({
                        ...previous,
                        [plan.id]: event.target.checked,
                      }))
                    }
                  />{' '}
                  I authorize real AWS resource creation and the associated charges. Application
                  cutover is not authorized.
                </label>
                <button
                  className="button primary"
                  disabled={
                    busy ||
                    !configuration?.enabled ||
                    !configuration.can_approve ||
                    configuration.operator_id === plan.created_by ||
                    confirmation[plan.id] !== plan.confirmation ||
                    !costsAccepted[plan.id] ||
                    Date.now() / 1000 > plan.expires_at
                  }
                  onClick={() => void run(() => api.approveRestore(plan, confirmation[plan.id]))}
                >
                  Approve real AWS restore
                </button>
              </div>
            )}
            <ol className="restore-events">
              {plan.events.map((event) => (
                <li key={`${event.at}-${event.title}`}>
                  <strong>{event.title}</strong>
                  <span>{event.detail}</span>
                </li>
              ))}
            </ol>
            <p className="live-footnote">
              Infrastructure restoration is not application recovery. Target locks remain in place
              until an administrator verifies the outcome and any cleanup. Restored resources are
              never automatically deleted.
            </p>
          </div>
        </section>
      ))}
    </div>
  );
}
