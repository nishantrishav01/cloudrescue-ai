import { Cloud, Database, Globe2, Layers3, Network, Server, ShieldCheck } from 'lucide-react';
import type { Incident } from '../types';

export function Topology({ incident }: { incident: Incident | null }) {
  const active =
    incident && (incident.status !== 'COMPLETED' || incident.scenario_id === 'network-failure');
  const database = active && ['db-outage', 'missing-standby'].includes(incident.scenario_id);
  const compute = active && incident.scenario_id === 'compute-overload';
  const network = active && incident.scenario_id === 'network-failure';
  return (
    <div className="topology" aria-label="Simulated AWS service topology">
      <div className="region-label">
        <Cloud size={13} /> AWS CLOUD <span>ap-south-1</span>
      </div>
      <svg
        className="connections"
        viewBox="0 0 800 330"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path d="M100 163 H235 M305 163 H420 M490 163 H610 V93 H690 M610 163 V245 H690" />
        <path
          className="flow"
          d="M100 163 H235 M305 163 H420 M490 163 H610 V93 H690 M610 163 V245 H690"
        />
      </svg>
      <div className="topology-node internet">
        <div className="node-icon">
          <Globe2 />
        </div>
        <strong>Internet</strong>
        <span>Incoming traffic</span>
      </div>
      <div className={`topology-node gateway ${network ? 'unhealthy' : ''}`}>
        <div className="node-icon purple">
          <Network />
        </div>
        <strong>Load balancer</strong>
        <span>Application gateway</span>
        <i className="node-status">{network ? 'Degraded' : 'Healthy'}</i>
      </div>
      <div className={`topology-node compute ${compute ? 'unhealthy' : ''}`}>
        <div className="node-icon orange">
          <Server />
        </div>
        <strong>Compute cluster</strong>
        <span>EC2 · Auto Scaling</span>
        <i className="node-status">{compute ? 'CPU at 98%' : 'Healthy'}</i>
      </div>
      <div className={`topology-node database ${database ? 'unhealthy' : ''}`}>
        <div className="node-icon blue">
          <Database />
        </div>
        <strong>Primary database</strong>
        <span>RDS · PostgreSQL</span>
        <i className="node-status">{database ? 'Unavailable' : 'Healthy'}</i>
      </div>
      <div className="topology-node standby">
        <div className="node-icon blue">
          <Layers3 />
        </div>
        <strong>Standby replica</strong>
        <span>Availability zone 1b</span>
        <i className="node-status">
          {incident?.scenario_id === 'missing-standby' && active ? 'Unknown' : 'Healthy'}
        </i>
      </div>
      <div className="topology-foot">
        <span>
          <i /> Virtual environment
        </span>
        <span>
          <ShieldCheck size={12} /> Read-only AWS access
        </span>
        <span className="topology-legend">
          <i /> Healthy <i className="red" /> Impacted
        </span>
      </div>
    </div>
  );
}
