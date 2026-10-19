import {
  ArrowUpRight,
  BrainCircuit,
  Check,
  CircleHelp,
  LockKeyhole,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import type { Incident } from '../types';

export function DecisionPanel({
  incident,
  busy,
  onApprove,
}: {
  incident: Incident | null;
  busy: boolean;
  onApprove: () => void;
}) {
  const decision = incident?.decision;
  const pending = incident?.status === 'ANALYZING';
  const blocked = incident && ['ESCALATED', 'FAILED'].includes(incident.status);
  return (
    <section className="panel decision-panel">
      <div className="panel-heading">
        <h2>
          <BrainCircuit size={17} /> Decision center
        </h2>
        <span className="tiny-label">STRANDS DECIDER 2B</span>
      </div>
      {decision ? (
        <>
          <div className="decision-title">
            <span className="ai-mark">
              <Sparkles size={19} />
            </span>
            <div>
              <span className="eyebrow">RECOMMENDED PLAYBOOK</span>
              <h3>{decision.choice.replaceAll('_', ' ')}</h3>
            </div>
            <ArrowUpRight size={20} />
          </div>
          <p className="decision-explanation">{incident.explanation}</p>
          <div className="confidence">
            <div>
              <span>Decision confidence</span>
              <strong>
                {Math.round(decision.confidence * 100)}
                <small>%</small>
              </strong>
            </div>
            <div className="progress-track">
              <span style={{ width: `${decision.confidence * 100}%` }} />
            </div>
            <span className="confidence-note">
              {decision.source === 'demo-fixture'
                ? 'Illustrative sample · not a model measurement'
                : `${(decision.inference_ms ?? decision.latency_ms).toFixed(0)} ms model · ${(decision.latency_ms / 1000).toFixed(1)} s request`}
            </span>
          </div>
          <div className="probabilities">
            {Object.entries(decision.probabilities)
              .sort((first, second) => second[1] - first[1])
              .map(([name, probability]) => (
                <div key={name}>
                  <span>{name.replaceAll('_', ' ')}</span>
                  <div>
                    <i style={{ width: `${probability * 100}%` }} />
                  </div>
                  <b>{(probability * 100).toFixed(1)}%</b>
                </div>
              ))}
          </div>
          <div className={`safety-message ${incident.validation?.allowed ? '' : 'blocked'}`}>
            <ShieldCheck size={16} />
            <span>{incident.validation?.reasons.join(' ')}</span>
          </div>
          <button
            className="button primary approve-button"
            disabled={busy || incident.status !== 'WAITING_APPROVAL'}
            onClick={onApprove}
          >
            {incident.status === 'COMPLETED' ? (
              <>
                <Check size={16} /> Simulation complete
              </>
            ) : incident.status === 'SIMULATING' ? (
              <>
                <span className="spinner" /> Simulating recovery
              </>
            ) : incident.status === 'ESCALATED' ? (
              <>
                <CircleHelp size={16} /> Operator investigation required
              </>
            ) : (
              <>
                <LockKeyhole size={15} /> Approve simulated recovery
              </>
            )}
          </button>
          <p className="approval-note">
            {incident.source === 'aws-live'
              ? 'Read-only investigation. Use a separately reviewed DR restore plan for any real AWS recovery.'
              : 'Your approval applies to this virtual playbook only.'}
          </p>
        </>
      ) : (
        <div className="decision-empty">
          <div className={`scan-orb ${pending ? 'scanning' : ''}`}>
            <BrainCircuit size={34} />
          </div>
          <h3>
            {pending
              ? 'Connecting the signals'
              : blocked
                ? 'Operator investigation required'
                : 'Clarity when it matters.'}
          </h3>
          <p>
            {pending
              ? 'Collecting evidence and evaluating the safest next step. You can follow progress in the incident timeline.'
              : blocked
                ? (incident.explanation ??
                  'The workflow could not complete. Inspect the timeline and service logs before retrying.')
                : 'Trigger a scenario to see how AI turns incident evidence into a clear, explainable recovery decision.'}
          </p>
          <div className="empty-steps">
            <span>
              <Check size={12} /> Evidence first
            </span>
            <span>
              <Check size={12} /> Human approved
            </span>
          </div>
        </div>
      )}
      <div className="panel-bottom">
        <span className="status-dot" />{' '}
        {decision?.source === 'demo-fixture'
          ? 'Demo decision fixture'
          : 'Model decisions · deterministic safety'}
      </div>
    </section>
  );
}
