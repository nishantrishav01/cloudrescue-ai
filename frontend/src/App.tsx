import { useEffect, useRef, useState } from 'react';
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Bell,
  BookOpen,
  ChartNoAxesCombined,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  Cloud,
  Command,
  ExternalLink,
  FlaskConical,
  History,
  LayoutDashboard,
  LifeBuoy,
  LogIn,
  LogOut,
  Menu,
  Plus,
  Radio,
  Search,
  ShieldCheck,
  Terminal,
  X,
  Zap,
} from 'lucide-react';
import { api, completeSignIn, config, isSignedIn, signIn, signOut } from './api';
import { scenarios as sampleScenarios } from './demo';
import { DecisionPanel } from './components/DecisionPanel';
import { Topology } from './components/Topology';
import { LiveResources } from './components/LiveResources';
import { DisasterRecovery } from './components/DisasterRecovery';
import type { Benchmark, Incident, Scenario } from './types';

const navigation = [
  { label: 'War room', icon: LayoutDashboard },
  { label: 'Live resources', icon: Cloud },
  { label: 'Disaster recovery', icon: ShieldCheck },
  { label: 'Incident history', icon: History },
  { label: 'Benchmarks', icon: ChartNoAxesCombined },
  { label: 'Runbook library', icon: BookOpen },
];
const statusLabel = (status?: string) =>
  status?.toLowerCase().replaceAll('_', ' ') ?? 'Ready to explore';
const dateTime = (value: string) =>
  new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

export default function App() {
  const [tab, setTab] = useState('War room');
  const [incident, setIncident] = useState<Incident | null>(null);
  const [history, setHistory] = useState<Incident[]>([]);
  const [scenarios, setScenarios] = useState<Scenario[]>(sampleScenarios);
  const [selected, setSelected] = useState('db-outage');
  const [dialog, setDialog] = useState(false);
  const [approval, setApproval] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [signedIn, setSignedIn] = useState(isSignedIn());
  const [sidebar, setSidebar] = useState(false);
  const [search, setSearch] = useState('');
  const [benchmark, setBenchmark] = useState<Benchmark | null>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const demo = config.mode === 'demo';
  const liveView = ['Live resources', 'Disaster recovery'].includes(tab);

  useEffect(() => {
    if (tab === 'Benchmarks' && (demo || signedIn)) {
      void api
        .benchmarks()
        .then(setBenchmark)
        .catch((issue: Error) => setError(issue.message));
    }
  }, [tab, demo, signedIn]);

  useEffect(() => {
    void completeSignIn()
      .then(() => setSignedIn(isSignedIn()))
      .catch((issue: Error) => setError(issue.message));
  }, []);
  useEffect(() => {
    if (!demo && !signedIn) return;
    void api
      .scenarios()
      .then(setScenarios)
      .catch((issue: Error) => setError(issue.message));
    void api
      .benchmarks()
      .then(setBenchmark)
      .catch((issue: Error) => setError(issue.message));
    if (!demo)
      void api
        .list()
        .then(setHistory)
        .catch((issue: Error) => setError(issue.message));
  }, [demo, signedIn]);
  useEffect(() => {
    if (!incident || !['ANALYZING', 'SIMULATING'].includes(incident.status)) return;
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
    }, 1400);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [incident?.id, incident?.status]);
  useEffect(() => {
    if (incident)
      setHistory((previous) => [incident, ...previous.filter((item) => item.id !== incident.id)]);
  }, [incident]);
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'k') {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener('keydown', keydown);
    return () => window.removeEventListener('keydown', keydown);
  }, []);
  useEffect(() => {
    if (dialog || approval) dialogRef.current?.showModal();
    else dialogRef.current?.close();
  }, [dialog, approval]);

  async function trigger() {
    setBusy(true);
    setError('');
    try {
      setIncident(await api.create(selected));
      setDialog(false);
      setTab('War room');
    } catch (issue) {
      setError((issue as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function approve() {
    if (!incident) return;
    setBusy(true);
    setError('');
    try {
      setIncident(await api.approve(incident));
      setApproval(false);
    } catch (issue) {
      setError((issue as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function downloadReport() {
    if (!incident?.report) return;
    const url = URL.createObjectURL(
      new Blob([incident.report], { type: 'text/markdown;charset=utf-8' }),
    );
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `cloudrescue-${incident.id}.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  }
  const liveIncident = incident && !['COMPLETED', 'ESCALATED', 'FAILED'].includes(incident.status);
  const degraded =
    incident && (incident.status !== 'COMPLETED' || incident.scenario_id === 'network-failure');
  const displayedScenario = scenarios.find((item) => item.id === incident?.scenario_id);
  const filteredHistory = history.filter((item) =>
    `${item.id} ${item.scenario_id} ${item.status}`.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="app-shell">
      <a href="#main" className="skip-link">
        Skip to war room
      </a>
      <aside className={`sidebar ${sidebar ? 'open' : ''}`}>
        <a className="brand" href="/" aria-label="CloudRescue AI home">
          <span className="brand-icon">
            <Zap size={21} fill="currentColor" />
          </span>
          <span>
            cloudrescue<span className="brand-ai">AI</span>
          </span>
        </a>
        <div className="workspace">
          <span className="workspace-avatar">
            <Cloud size={17} />
          </span>
          <div>
            <strong>CloudRescue workspace</strong>
            <span>Disaster recovery platform</span>
          </div>
          <ChevronDown size={13} />
        </div>
        <span className="nav-heading">WORKSPACE</span>
        <nav aria-label="Main navigation">
          {navigation.map((item) => (
            <button
              key={item.label}
              className={`nav-item ${tab === item.label ? 'active' : ''}`}
              onClick={() => {
                setTab(item.label);
                if (item.label === 'War room' && incident?.source === 'aws-live') setIncident(null);
                setSidebar(false);
              }}
            >
              <item.icon size={17} />
              <span>{item.label}</span>
              {item.label === 'War room' ? (
                <span className="live-tag">LIVE</span>
              ) : item.label === 'Incident history' && history.length > 0 ? (
                <span className="nav-count">{history.length}</span>
              ) : null}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="safe-card">
            <div>
              <ShieldCheck size={17} />
              <span>A safe place to break things.</span>
            </div>
            <p>
              Live evidence. Explicit approvals.
              <br />
              Your infrastructure stays yours.
            </p>
            <span className="safe-badge">
              <i /> {liveView ? 'CONTROLLED AWS ACCESS' : 'SCENARIOS ARE SIMULATED'}
            </span>
          </div>
          <button className="nav-item" onClick={() => setTab('How it works')}>
            <CircleHelp size={17} /> Help & documentation <ExternalLink size={13} />
          </button>
          <div className="operator">
            <span className="operator-avatar">CR</span>
            <div>
              <strong>{signedIn ? 'Signed-in operator' : 'Cloud operator'}</strong>
              <span>{demo ? 'Demo workspace' : 'AWS workspace'}</span>
            </div>
            <button
              className="icon-button"
              aria-label={signedIn ? 'Sign out' : 'Sign in'}
              onClick={() =>
                signedIn
                  ? signOut()
                  : void signIn().catch((issue: Error) => setError(issue.message))
              }
            >
              {signedIn ? <LogOut size={16} /> : <LogIn size={16} />}
            </button>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              aria-label="Toggle menu"
              onClick={() => setSidebar(!sidebar)}
            >
              <Menu size={19} />
            </button>
            <Cloud size={16} />
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>{tab}</strong>
          </div>
          <div className="topbar-right">
            <label className="search">
              <Search size={14} />
              <input
                ref={searchRef}
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  if (event.target.value) setTab('Incident history');
                }}
                placeholder="Search incidents..."
                aria-label="Search incidents"
              />
              <kbd>
                <Command size={10} /> K
              </kbd>
            </label>
            <span className="topbar-divider" />
            <button
              className="icon-button notification"
              aria-label="View incident updates"
              onClick={() => setTab('Incident history')}
            >
              <Bell size={17} />
              <i />
            </button>
            <span className="small-avatar">CR</span>
          </div>
        </header>
        <main id="main">
          <div className="mode-banner">
            <span>
              <FlaskConical size={14} />
              <strong>
                {demo
                  ? 'Interactive demo'
                  : liveView
                    ? 'Live AWS workspace'
                    : 'AWS simulation workspace'}
              </strong>
              <span>
                {demo
                  ? 'Explore safely. All telemetry and AI decisions in this preview are illustrative.'
                  : liveView
                    ? 'Real inventory and CloudWatch evidence. Real restores require configured scope and independent approval.'
                    : 'Synthetic incident evidence. Real AI decisions. Recovery actions in these scenarios are simulated.'}
              </span>
            </span>
            <span className="banner-pill">
              {demo
                ? 'NO AWS CHANGES'
                : liveView
                  ? 'EXPLICIT RESTORE APPROVAL'
                  : 'SYNTHETIC SCENARIOS'}
            </span>
          </div>
          {error && (
            <div className="error-banner" role="alert">
              <span>{error}</span>
              <button
                className="icon-button"
                aria-label="Dismiss error"
                onClick={() => setError('')}
              >
                <X size={15} />
              </button>
            </div>
          )}
          <div className="page-heading">
            <div>
              <div className="eyebrow">
                <span className="status-dot" /> YOUR CLOUD. UNDER CONTROL.
              </div>
              <h1>{tab === 'War room' ? 'Incident war room' : tab}</h1>
              <p>
                {tab === 'War room'
                  ? 'From the first signal to a confident recovery. All in one place.'
                  : tab === 'Incident history'
                    ? 'Every signal, decision, and approval. A complete record.'
                    : tab === 'Benchmarks'
                      ? 'Trust is built on evidence. Explore actual model measurements.'
                      : tab === 'Live resources'
                        ? 'Actual AWS inventory, timestamped measurements and read-only investigation.'
                        : tab === 'Disaster recovery'
                          ? 'Review your recovery scope, prerequisites and independent approvals.'
                          : 'Understand your recovery tools before you need them.'}
              </p>
            </div>
            <div className="heading-actions">
              {!demo && !signedIn && (
                <button
                  className="button secondary"
                  onClick={() => void signIn().catch((issue: Error) => setError(issue.message))}
                >
                  <LogIn size={15} /> Operator sign-in
                </button>
              )}
              <button
                className="button primary"
                onClick={() => {
                  setApproval(false);
                  setDialog(true);
                }}
                disabled={!demo && !signedIn}
              >
                <Plus size={17} /> {liveView ? 'Simulate incident' : 'Trigger incident'}
              </button>
            </div>
          </div>
          {tab === 'Live resources' && (
            <LiveResources
              signedIn={signedIn}
              initialIncident={incident?.source === 'aws-live' ? incident : null}
            />
          )}
          {tab === 'Disaster recovery' && <DisasterRecovery signedIn={signedIn} />}
          {tab === 'War room' && (
            <>
              <div className="stats-grid">
                <Stat
                  icon={Activity}
                  label="Environment health"
                  value={degraded ? 'Degraded' : 'Healthy'}
                  note={
                    degraded ? 'Virtual incident needs attention' : 'Virtual services operational'
                  }
                  color={degraded ? 'red' : 'green'}
                >
                  <Sparkline />
                </Stat>
                <Stat
                  icon={Radio}
                  label="Active incidents"
                  value={liveIncident ? '01' : '00'}
                  note={
                    liveIncident
                      ? (displayedScenario?.name ?? 'Investigating incident')
                      : 'A quiet moment. Stay prepared.'
                  }
                  color="orange"
                >
                  <span className="stat-subtle">{history.length} total</span>
                </Stat>
                <Stat
                  icon={Clock3}
                  label="Recovery target"
                  value="15"
                  suffix="min"
                  note="Scenario objective · not a measurement"
                  color="purple"
                >
                  <span className="target-icon">
                    <Clock3 size={24} />
                  </span>
                </Stat>
                <Stat
                  icon={ShieldCheck}
                  label="Safety posture"
                  value="Protected"
                  note="Every recovery needs your approval"
                  color="blue"
                >
                  <span className="protected-icon">
                    <ShieldCheck size={26} />
                  </span>
                </Stat>
              </div>
              <div className="war-grid">
                <div className="war-left">
                  <section className="panel topology-panel">
                    <div className="panel-heading">
                      <div>
                        <h2>
                          <NetworkIcon /> Infrastructure map
                        </h2>
                        <span className="panel-subheading">
                          A clear view of your simulated environment
                        </span>
                      </div>
                      <div className="map-controls">
                        <span className="region-pill">
                          <i /> {config.region ?? 'ap-south-1'}
                        </span>
                        <span className="tiny-label">5 SERVICES</span>
                      </div>
                    </div>
                    <Topology incident={incident} />
                  </section>
                  <section className="panel timeline-panel">
                    <div className="panel-heading">
                      <h2>
                        <Activity size={17} /> Incident timeline
                      </h2>
                      <span
                        className={`status-pill ${incident?.status === 'COMPLETED' ? 'resolved' : ''}`}
                      >
                        <i /> {statusLabel(incident?.status)}
                      </span>
                    </div>
                    {incident ? (
                      <>
                        <div className="incident-summary">
                          <span className="incident-severity">
                            {displayedScenario?.severity ?? 'High'}
                          </span>
                          <strong>{displayedScenario?.name ?? incident.scenario_id}</strong>
                          <span className="incident-id">
                            INC-{incident.id.slice(0, 8).toUpperCase()}
                          </span>
                        </div>
                        <ol className="timeline">
                          {incident.events.map((item, index) => (
                            <li
                              key={`${index}-${item.title}`}
                              style={{ animationDelay: `${index * 60}ms` }}
                            >
                              <span className="timeline-dot">
                                <Check size={11} />
                              </span>
                              <div>
                                <strong>{item.title}</strong>
                                <p>{item.detail}</p>
                              </div>
                              <time>{dateTime(item.at)}</time>
                            </li>
                          ))}
                        </ol>
                        {incident.status === 'ANALYZING' || incident.status === 'SIMULATING' ? (
                          <div className="timeline-loading">
                            <span className="spinner" />{' '}
                            {incident.status === 'ANALYZING'
                              ? 'Analyzing the incident...'
                              : 'Running the approved virtual playbook...'}
                          </div>
                        ) : null}
                        {incident.report && (
                          <button className="report-link" onClick={downloadReport}>
                            <ArrowDownToLine size={15} /> Download incident report{' '}
                            <ArrowRight size={14} />
                          </button>
                        )}
                      </>
                    ) : (
                      <div className="timeline-empty">
                        <span className="empty-timeline-icon">
                          <Terminal size={23} />
                        </span>
                        <div>
                          <h3>Your next recovery story starts here.</h3>
                          <p>Trigger a scenario and watch each step unfold in real time.</p>
                        </div>
                        <button
                          className="icon-button"
                          aria-label="Start a scenario"
                          onClick={() => setDialog(true)}
                        >
                          <ArrowRight size={21} />
                        </button>
                      </div>
                    )}
                  </section>
                </div>
                <DecisionPanel
                  incident={incident}
                  busy={busy}
                  onApprove={() => {
                    setDialog(false);
                    setApproval(true);
                  }}
                />
              </div>
              <section className="scenario-section">
                <div className="section-title">
                  <h2>Practice for the unexpected</h2>
                  <span>Choose a scenario. Build your confidence.</span>
                  <button className="text-button" onClick={() => setTab('Runbook library')}>
                    View all runbooks <ArrowRight size={13} />
                  </button>
                </div>
                <div className="scenario-grid">
                  {scenarios.slice(0, 3).map((scenario, index) => (
                    <button
                      className="scenario-card"
                      key={scenario.id}
                      onClick={() => {
                        setSelected(scenario.id);
                        setApproval(false);
                        setDialog(true);
                      }}
                    >
                      <span className={`scenario-icon scenario-${index}`}>
                        {index === 0 ? (
                          <Cloud size={20} />
                        ) : index === 1 ? (
                          <Activity size={20} />
                        ) : (
                          <Radio size={20} />
                        )}
                      </span>
                      <div>
                        <h3>{scenario.name}</h3>
                        <p>{scenario.description}</p>
                        <span>
                          {scenario.service} <i /> {scenario.severity}
                        </span>
                      </div>
                      <ArrowUpIcon />
                    </button>
                  ))}
                </div>
              </section>
              {incident && (
                <section className="panel evidence-panel">
                  <div className="panel-heading">
                    <h2>
                      <Search size={16} /> Evidence & provenance
                    </h2>
                    <span className="tiny-label">{incident.source}</span>
                  </div>
                  <div className="evidence-grid">
                    {Object.entries(incident.observations).map(([key, value]) => (
                      <div key={key}>
                        <span>{key.replaceAll('_', ' ')}</span>
                        <strong>{String(value ?? 'Unknown')}</strong>
                      </div>
                    ))}
                  </div>
                  {incident.decision && (
                    <p className="model-provenance">
                      Model: {incident.decision.model_id} · Evidence hash:{' '}
                      {incident.decision.state_hash}
                    </p>
                  )}
                </section>
              )}
            </>
          )}
          {tab === 'Incident history' && (
            <section className="panel history-panel">
              <div className="panel-heading">
                <h2>Incident ledger</h2>
                <span className="tiny-label">{filteredHistory.length} INCIDENTS</span>
              </div>
              {filteredHistory.length ? (
                <div className="history-list">
                  {filteredHistory.map((item) => (
                    <button
                      key={item.id}
                      onClick={() => {
                        setIncident(item);
                        setTab(item.source === 'aws-live' ? 'Live resources' : 'War room');
                      }}
                    >
                      <span className="history-symbol">
                        <Activity size={18} />
                      </span>
                      <div>
                        <strong>
                          {scenarios.find((scenario) => scenario.id === item.scenario_id)?.name ??
                            item.scenario_id}
                        </strong>
                        <span>
                          {item.id.slice(0, 8)} · {new Date(item.created_at).toLocaleString()}
                        </span>
                      </div>
                      <span className="status-pill">{statusLabel(item.status)}</span>
                      <ChevronRight size={16} />
                    </button>
                  ))}
                </div>
              ) : (
                <div className="content-empty">
                  <History size={35} />
                  <h3>{search ? 'No matching incidents' : 'A clean slate.'}</h3>
                  <p>
                    {search
                      ? 'Try a different incident ID, scenario, or status.'
                      : 'Your incidents will appear here when you run a scenario.'}
                  </p>
                </div>
              )}
            </section>
          )}
          {tab === 'Benchmarks' && (
            <section className="panel">
              <div className="panel-heading">
                <h2>
                  <ChartNoAxesCombined size={17} /> Model evaluation
                </h2>
                <span className="tiny-label">MEASUREMENTS ONLY</span>
              </div>
              <div className="benchmark-grid">
                <div>
                  <span>Your incident inferences</span>
                  <strong>{benchmark?.count ?? 0}</strong>
                </div>
                <div>
                  <span>Average incident inference request</span>
                  <strong>
                    {benchmark?.average_latency_ms == null
                      ? '—'
                      : `${benchmark.average_latency_ms.toFixed(0)} ms`}
                  </strong>
                </div>
                <div>
                  <span>Classification accuracy</span>
                  <strong>
                    {benchmark?.evaluation
                      ? `${(benchmark.evaluation.accuracy * 100).toFixed(1)}%`
                      : 'Not evaluated'}
                  </strong>
                </div>
              </div>
              {benchmark?.evaluation && (
                <>
                  <div className="panel-heading">
                    <h2>Labeled evaluation · {benchmark.evaluation.case_count} cases</h2>
                    <span className="tiny-label">{benchmark.evaluation.runtime}</span>
                  </div>
                  <div className="benchmark-grid">
                    <div>
                      <span>Warm prediction p50 / p95</span>
                      <strong>
                        {(benchmark.evaluation.warm_p50_ms / 1000).toFixed(2)} /{' '}
                        {(benchmark.evaluation.warm_p95_ms / 1000).toFixed(2)} s
                      </strong>
                    </div>
                    <div>
                      <span>Model loading</span>
                      <strong>{(benchmark.evaluation.model_load_ms / 1000).toFixed(2)} s</strong>
                    </div>
                    <div>
                      <span>Unsafe recommendations allowed</span>
                      <strong>{benchmark.evaluation.unsafe_recommendations_allowed}</strong>
                    </div>
                  </div>
                  <div className="benchmark-table-wrap">
                    <table className="benchmark-table">
                      <thead>
                        <tr>
                          <th>Case</th>
                          <th>Expected</th>
                          <th>Model selected</th>
                          <th>Confidence</th>
                          <th>Time</th>
                          <th>Policy</th>
                        </tr>
                      </thead>
                      <tbody>
                        {benchmark.evaluation.cases.map((row) => (
                          <tr key={row.id}>
                            <td>{row.id}</td>
                            <td>{row.expected.replaceAll('_', ' ')}</td>
                            <td
                              className={row.correct ? 'benchmark-correct' : 'benchmark-mismatch'}
                            >
                              {row.predicted?.replaceAll('_', ' ') ?? 'Inference error'}
                            </td>
                            <td>
                              {row.confidence == null
                                ? '—'
                                : `${(row.confidence * 100).toFixed(1)}%`}
                            </td>
                            <td>
                              {row.inference_ms == null
                                ? '—'
                                : `${(row.inference_ms / 1000).toFixed(2)} s`}
                            </td>
                            <td>{row.safety_allowed ? 'Approval eligible' : 'Escalate'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="benchmark-caption">
                    {benchmark.evaluation.dataset} · {benchmark.evaluation.correct}/
                    {benchmark.evaluation.case_count} correct · {benchmark.evaluation.vcpus} vCPUs /{' '}
                    {benchmark.evaluation.memory_mib / 1024} GiB ·{' '}
                    {new Date(benchmark.evaluation.completed_at).toLocaleString()}
                  </p>
                </>
              )}
              <div className="notice">
                <CircleHelp size={17} />
                <p>
                  {benchmark?.evaluation?.notes ??
                    'Demo confidence is illustrative. Labeled accuracy and model timings appear after a real evaluation run. Incident request times include container startup and invocation overhead. No disaster-recovery RTO or RPO is measured here.'}
                </p>
              </div>
            </section>
          )}
          {tab === 'Runbook library' && (
            <div className="runbook-grid">
              {scenarios.map((scenario, index) => (
                <section className="panel runbook" key={scenario.id}>
                  <span className="eyebrow">PLAYBOOK {String(index + 1).padStart(2, '0')}</span>
                  <h2>{scenario.name}</h2>
                  <p>{scenario.description}</p>
                  <div>
                    <span className="region-pill">{scenario.service}</span>
                    <span className="tiny-label">{scenario.severity}</span>
                  </div>
                  <button
                    className="button secondary"
                    onClick={() => {
                      setSelected(scenario.id);
                      setApproval(false);
                      setDialog(true);
                    }}
                  >
                    Explore scenario <ArrowRight size={14} />
                  </button>
                </section>
              ))}
            </div>
          )}
          {tab === 'How it works' && (
            <section className="panel documentation">
              <LifeBuoy size={28} />
              <h2>Prepared teams recover with confidence.</h2>
              <p>
                Choose a scenario to create a virtual incident. The AWS workflow collects evidence,
                calls the isolated Strands Decider model, and validates its recommendation against
                deterministic safety rules. Amazon Bedrock explains the recorded decision.
              </p>
              <ol>
                <li>Inspect symptoms, provenance, model choice, and confidence.</li>
                <li>Approve a matching, current virtual playbook.</li>
                <li>Watch the simulated recovery and export the incident report.</li>
              </ol>
              <p>
                Ambiguous evidence, an unavailable model, and invalid prerequisites escalate to an
                operator. The simulator has no AWS infrastructure modification permissions.
              </p>
              <a
                href="https://github.com/strands-labs/strands-decider"
                target="_blank"
                rel="noreferrer"
                className="text-button"
              >
                Explore Strands Decider <ExternalLink size={13} />
              </a>
            </section>
          )}
          <footer>
            <span>
              <Zap size={12} /> Built for calm. Ready for chaos.
            </span>
            <span>
              CloudRescue AI <i /> {demo ? 'Interactive preview' : 'AWS connected'}{' '}
              <span className="footer-status">
                <i /> {liveView ? 'Controlled AWS access' : 'Simulation mode'}
              </span>
            </span>
          </footer>
        </main>
      </div>
      <dialog
        ref={dialogRef}
        onCancel={() => {
          setDialog(false);
          setApproval(false);
        }}
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            setDialog(false);
            setApproval(false);
          }
        }}
      >
        <div className="modal">
          <button
            className="icon-button modal-close"
            aria-label="Close dialog"
            onClick={() => {
              setDialog(false);
              setApproval(false);
            }}
          >
            <X size={20} />
          </button>
          <span className="modal-symbol">
            {approval ? <ShieldCheck size={26} /> : <Zap size={26} />}
          </span>
          <span className="eyebrow">
            {approval ? 'YOU ARE IN CONTROL' : 'BREAK THINGS. LEARN SAFELY.'}
          </span>
          <h2>{approval ? 'Approve virtual recovery?' : 'Choose your incident'}</h2>
          <p>
            {approval
              ? `Run ${incident?.decision?.choice.replaceAll('_', ' ')} against the virtual incident. This approval is bound to the current decision and can be used once.`
              : 'Explore an outage in a safe, isolated environment. No real AWS infrastructure will be changed.'}
          </p>
          {!approval && (
            <div className="scenario-options">
              {scenarios.map((item) => (
                <label key={item.id} className={selected === item.id ? 'selected' : ''}>
                  <input
                    type="radio"
                    name="scenario"
                    value={item.id}
                    checked={selected === item.id}
                    onChange={() => setSelected(item.id)}
                  />
                  <div>
                    <strong>{item.name}</strong>
                    <span>{item.description}</span>
                  </div>
                  <span className="option-indicator">
                    {selected === item.id && <Check size={12} />}
                  </span>
                </label>
              ))}
            </div>
          )}
          {error && (
            <p role="alert" className="modal-error">
              {error}
            </p>
          )}
          <button
            className="button primary modal-action"
            disabled={busy || (!demo && !signedIn)}
            onClick={() => void (approval ? approve() : trigger())}
          >
            {busy ? (
              <span className="spinner" />
            ) : approval ? (
              <ShieldCheck size={16} />
            ) : (
              <Zap size={16} />
            )}
            {busy ? 'Working...' : approval ? 'Approve simulation' : 'Launch scenario'}
            <ArrowRight size={16} />
          </button>
          <span className="modal-safety">
            <ShieldCheck size={13} />{' '}
            {demo
              ? 'Deterministic preview · no real inference'
              : 'Authenticated · audited · simulation only'}
          </span>
        </div>
      </dialog>
    </div>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
  suffix,
  note,
  color,
  children,
}: {
  icon: typeof Activity;
  label: string;
  value: string;
  suffix?: string;
  note: string;
  color: string;
  children: React.ReactNode;
}) {
  return (
    <section className={`stat-card stat-${color}`}>
      <div className="stat-heading">
        <span>{label}</span>
        <Icon size={15} />
      </div>
      <div className="stat-value">
        {value}
        <small>{suffix}</small>
        {children}
      </div>
      <div className="stat-note">
        <i />
        {note}
      </div>
    </section>
  );
}
function Sparkline() {
  return (
    <svg className="sparkline" viewBox="0 0 105 35" aria-hidden="true">
      <path d="M0 28 10 27 16 15 24 20 34 16 42 21 48 10 55 14 60 5 67 12 75 9 82 14 89 6 97 9 105 3" />
    </svg>
  );
}
function NetworkIcon() {
  return <Cloud size={17} />;
}
function ArrowUpIcon() {
  return (
    <svg
      className="arrow-up-icon"
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
    >
      <path d="M4 12 12 4M4 4h8v8" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}
