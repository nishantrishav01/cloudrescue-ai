import { useEffect, useState } from 'react';
import { Activity, RefreshCw, Search, ShieldCheck } from 'lucide-react';
import { api, config } from '../api';
import type { Incident, LiveInventory } from '../types';
import { DecisionPanel } from './DecisionPanel';

export function LiveResources({
  signedIn,
  initialIncident = null,
}: {
  signedIn: boolean;
  initialIncident?: Incident | null;
}) {
  const [inventory, setInventory] = useState<LiveInventory | null>(null);
  const [incident, setIncident] = useState<Incident | null>(initialIncident);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [search, setSearch] = useState('');
  const connected = config.mode === 'aws' && signedIn;
  useEffect(() => {
    if (!connected) return;
    let cancelled = false;
    const load = () =>
      void api
        .liveInventory()
        .then((value) => {
          if (!cancelled) setInventory(value);
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
  useEffect(() => {
    if (!incident || incident.status !== 'ANALYZING') return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void api
        .get(incident.id)
        .then((value) => {
          if (!cancelled) setIncident(value);
        })
        .catch((issue: Error) => {
          if (!cancelled) setError(issue.message);
        });
    }, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [incident?.id, incident?.status]);
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (issue) {
      setError((issue as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const age = inventory?.collected_at
    ? Math.max(0, Math.floor((Date.now() - Date.parse(inventory.collected_at)) / 1000))
    : null;
  return (
    <div className="live-layout">
      <section className="panel">
        <div className="panel-heading">
          <h2>
            <Activity size={17} /> Live AWS resources
          </h2>
          <span className="tiny-label">READ ONLY</span>
        </div>
        <div className="live-content">
          <p>
            Actual AWS inventory, CloudWatch measurements and alarms. Resource state is not
            application health. Missing measurements are never shown as zero.
          </p>
          {!connected && (
            <div className="safety-message">
              Sign in to the deployed AWS workspace. No illustrative inventory is substituted here.
            </div>
          )}
          {error && (
            <p className="error-banner" role="alert">
              {error}
            </p>
          )}
          {notice && <p role="status">{notice}</p>}
          <div className="live-controls">
            <div>
              <strong>{inventory?.account_id ?? 'Account not connected'}</strong>
              <p>
                {inventory?.region ?? 'Region unavailable'} ·{' '}
                {age === null
                  ? 'Awaiting discovery'
                  : `Snapshot ${age}s old${age > 600 ? ' — STALE' : ''}`}
              </p>
            </div>
            <button
              className="button secondary"
              disabled={!connected || busy}
              onClick={() =>
                void run(async () => {
                  await api.refreshInventory();
                  setNotice(
                    'Discovery requested. This page checks for the updated snapshot every 15 seconds.',
                  );
                })
              }
            >
              <RefreshCw size={15} /> Refresh discovery
            </button>
          </div>
          {inventory?.errors.map((message) => (
            <p className="safety-message blocked" key={message}>
              {message}
            </p>
          ))}
          {inventory?.truncated && (
            <p className="safety-message blocked">
              Inventory reached its per-service limit of 100 resources and is incomplete.
            </p>
          )}
          <label className="live-filter">
            <Search size={16} />
            <input
              aria-label="Filter live resources"
              placeholder="Filter by name, type or resource ID"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <div className="live-resource-grid">
            {inventory?.resources
              .filter((resource) =>
                `${resource.name} ${resource.kind} ${resource.id}`
                  .toLowerCase()
                  .includes(search.toLowerCase()),
              )
              .map((resource) => (
                <article className="live-resource" key={resource.arn}>
                  <div className="live-resource-title">
                    <span className="eyebrow">{resource.kind}</span>
                    <span>{resource.state}</span>
                  </div>
                  <h3>{resource.name}</h3>
                  <code>{resource.id}</code>
                  <dl>
                    {resource.metrics.map((metric) => (
                      <div key={metric.name}>
                        <dt>
                          {metric.name}{' '}
                          <small>
                            ({metric.statistic}, {metric.period_seconds}s)
                          </small>
                        </dt>
                        <dd>
                          {metric.value === null
                            ? 'No measurement'
                            : new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(
                                metric.value,
                              )}
                          <small>
                            {metric.timestamp
                              ? `${new Date(metric.timestamp).toLocaleString()} · ${metric.status}`
                              : 'Timestamp unavailable'}
                          </small>
                        </dd>
                      </div>
                    ))}
                  </dl>
                  <p>
                    {resource.alarms.length
                      ? resource.alarms.map((alarm) => `${alarm.name}: ${alarm.state}`).join(' · ')
                      : 'No matching metric alarm found — not proof of health.'}
                  </p>
                  <span className="live-protection">DR protection: not yet verified</span>
                  <button
                    className="button secondary"
                    disabled={busy || incident?.status === 'ANALYZING' || age === null || age > 600}
                    onClick={() =>
                      void run(async () => {
                        setIncident(await api.investigate(resource.arn));
                      })
                    }
                  >
                    Investigate with AI
                  </button>
                </article>
              ))}
          </div>
          {inventory && !inventory.resources.length && (
            <p>
              No resources returned in this snapshot. Check discovery errors and the account/region
              scope.
            </p>
          )}
          <p className="live-footnote">
            <ShieldCheck size={14} /> No resource is stopped, changed, backed up or enrolled
            automatically. S3 storage metrics are daily; BucketSizeBytes covers StandardStorage, not
            all storage classes.
          </p>
        </div>
      </section>
      {incident && (
        <div>
          <p className="safety-message">
            Live investigation {incident.id}. Evidence is real; this analysis cannot authorize a
            restore.
          </p>
          <DecisionPanel incident={incident} busy={busy} onApprove={() => undefined} />
        </div>
      )}
    </div>
  );
}
