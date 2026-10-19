import type {
  Benchmark,
  Incident,
  LiveInventory,
  RecoveryConfiguration,
  RestorePlan,
  RuntimeConfig,
  Scenario,
} from './types';
import { approveDemo, createDemo, readDemo, scenarios } from './demo';

export const config: RuntimeConfig = await fetch('/config.json', { cache: 'no-store' })
  .then(async (response) => {
    if (!response.ok) throw new Error('Runtime configuration unavailable');
    return (await response.json()) as RuntimeConfig;
  })
  .catch(() => ({ mode: import.meta.env.DEV ? 'demo' : 'aws' }) as RuntimeConfig);

export function isSignedIn() {
  return Number(sessionStorage.getItem('expires_at') ?? 0) > Date.now();
}

export async function signIn() {
  if (!config.authDomain || !config.clientId)
    throw new Error('Operator sign-in is not configured.');
  const verifier = Array.from(crypto.getRandomValues(new Uint8Array(48)), (value) =>
    value.toString(16).padStart(2, '0'),
  ).join('');
  const encode = (bytes: Uint8Array) =>
    btoa(String.fromCharCode(...bytes))
      .replaceAll('+', '-')
      .replaceAll('/', '_')
      .replaceAll('=', '');
  const challenge = encode(
    new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))),
  );
  const state = crypto.randomUUID();
  sessionStorage.setItem('pkce_verifier', verifier);
  sessionStorage.setItem('oauth_state', state);
  const query = new URLSearchParams({
    response_type: 'code',
    client_id: config.clientId,
    redirect_uri: window.location.origin,
    scope: 'openid email',
    code_challenge: challenge,
    code_challenge_method: 'S256',
    state,
  });
  window.location.assign(`https://${config.authDomain}/oauth2/authorize?${query}`);
}

let signInPromise: Promise<void> | undefined;

export function completeSignIn() {
  signInPromise ??= exchangeSignIn();
  return signInPromise;
}

async function exchangeSignIn() {
  const query = new URLSearchParams(window.location.search);
  if (query.has('error')) throw new Error(query.get('error_description') ?? 'Sign-in failed.');
  const code = query.get('code');
  if (!code) return;
  if (query.get('state') !== sessionStorage.getItem('oauth_state'))
    throw new Error('Invalid sign-in state. Please sign in again.');
  const response = await fetch(`https://${config.authDomain}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: config.clientId ?? '',
      code,
      redirect_uri: window.location.origin,
      code_verifier: sessionStorage.getItem('pkce_verifier') ?? '',
    }),
  });
  const tokens = (await response.json()) as { access_token?: string; expires_in: number };
  if (!response.ok || !tokens.access_token) throw new Error('Unable to complete operator sign-in.');
  sessionStorage.setItem('access_token', tokens.access_token);
  sessionStorage.setItem('expires_at', String(Date.now() + tokens.expires_in * 1000));
  sessionStorage.removeItem('pkce_verifier');
  sessionStorage.removeItem('oauth_state');
  window.history.replaceState({}, '', window.location.pathname);
}

export function signOut() {
  sessionStorage.clear();
  if (config.authDomain && config.clientId) {
    window.location.assign(
      `https://${config.authDomain}/logout?${new URLSearchParams({ client_id: config.clientId, logout_uri: window.location.origin })}`,
    );
  } else window.location.reload();
}

async function request<Result>(path: string, body?: unknown): Promise<Result> {
  if (!config.apiUrl)
    throw new Error('AWS runtime is not configured. Deploy the CDK stack to connect the war room.');
  if (!isSignedIn()) throw new Error('Sign in as an operator to connect to AWS.');
  const response = await fetch(`${config.apiUrl.replace(/\/$/, '')}${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      Authorization: `Bearer ${sessionStorage.getItem('access_token')}`,
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(error.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<Result>;
}

export const api = {
  liveInventory: () => request<LiveInventory>('/api/live/resources'),
  refreshInventory: () => request<{ status: string }>('/api/live/refresh', {}),
  investigate: (resource_arn: string) =>
    request<Incident>('/api/live/investigations', { resource_arn }),
  recoveryConfiguration: () => request<RecoveryConfiguration>('/api/dr/configuration'),
  restorePlans: () => request<RestorePlan[]>('/api/dr/plans'),
  createRestore: (target_id: string) => request<RestorePlan>('/api/dr/plans', { target_id }),
  approveRestore: (plan: RestorePlan, confirmation: string) =>
    request<RestorePlan>(`/api/dr/plans/${plan.id}/approve`, {
      revision: plan.revision,
      approval_digest: plan.approval_digest,
      confirmation,
      accept_aws_charges: true,
    }),
  scenarios: () =>
    config.mode === 'demo' ? Promise.resolve(scenarios) : request<Scenario[]>('/api/scenarios'),
  create: (scenario_id: string) =>
    config.mode === 'demo'
      ? Promise.resolve(createDemo(scenario_id))
      : request<Incident>('/api/incidents', { scenario_id }),
  get: (id: string) =>
    config.mode === 'demo'
      ? Promise.resolve(readDemo(id))
      : request<Incident>(`/api/incidents/${id}`),
  list: () => request<Incident[]>('/api/incidents'),
  approve: (incident: Incident) =>
    config.mode === 'demo'
      ? Promise.resolve(approveDemo(incident.id))
      : request<Incident>(`/api/incidents/${incident.id}/approve`, {
          revision: incident.revision,
          decision_digest: incident.approval_digest,
        }),
  benchmarks: () =>
    config.mode === 'demo'
      ? Promise.resolve({
          count: 0,
          average_latency_ms: null,
          source: 'No measured inference yet',
        } satisfies Benchmark)
      : request<Benchmark>('/api/benchmarks'),
};
