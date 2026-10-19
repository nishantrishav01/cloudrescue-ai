import { test, expect } from '@playwright/test';

test('live inventory stays distinct from synthetic scenarios and DR is blocked without targets', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (issue) => errors.push(issue.message));
  await page.addInitScript(() => {
    sessionStorage.setItem('access_token', 'browser-test-fixture');
    sessionStorage.setItem('expires_at', String(Date.now() + 300000));
  });
  await page.route('**/config.json', (route) =>
    route.fulfill({ json: { mode: 'aws', apiUrl: 'http://127.0.0.1:5173/test-aws' } }),
  );
  await page.route('**/test-aws/api/**', (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const fixtures: Record<string, unknown> = {
      '/test-aws/api/scenarios': [],
      '/test-aws/api/incidents': [],
      '/test-aws/api/benchmarks': { count: 0, average_latency_ms: null },
      '/test-aws/api/live/resources': {
        source: 'aws-live',
        account_id: '123456789012',
        region: 'ap-south-1',
        collected_at: new Date().toISOString(),
        complete: true,
        errors: [],
        resources: [
          {
            id: 'i-test',
            name: 'Test workload',
            arn: 'arn:aws:ec2:ap-south-1:123456789012:instance/i-test',
            kind: 'EC2',
            state: 'running',
            region: 'ap-south-1',
            metrics: [
              {
                name: 'CPUUtilization',
                value: null,
                timestamp: null,
                status: 'missing',
                period_seconds: 300,
                statistic: 'Average',
              },
            ],
            alarms: [],
          },
        ],
      },
      '/test-aws/api/dr/configuration': {
        enabled: false,
        targets: [],
        operator_id: 'test-operator',
        can_approve: false,
        policy: {
          account_id: '123456789012',
          primary_region: 'ap-south-1',
          dr_region: 'ap-southeast-1',
          rto_seconds: 3600,
          rpo_seconds: 900,
          resource_types: ['EC2', 'RDS', 'Lambda'],
          scope: 'all-discovered-in-account',
          approver_group: 'RecoveryApprovers',
          automatic_cutover: false,
        },
      },
      '/test-aws/api/dr/plans': [],
    };
    return route.fulfill({ json: fixtures[pathname] ?? {} });
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.getByRole('button', { name: 'Live resources', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Test workload' })).toBeVisible();
  await expect(page.locator('.live-resource dd')).toContainText('No measurement');
  await expect(page.getByText('DR protection: not yet verified')).toBeVisible();
  await page.screenshot({ path: 'artifacts/live-resources-test.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBeTruthy();
  await page.screenshot({ path: 'artifacts/live-resources-test-mobile.png', fullPage: true });
  await page.getByRole('button', { name: 'Toggle menu' }).click();
  await page.getByRole('button', { name: 'Disaster recovery', exact: true }).click();
  await expect(page.getByText('REAL RESTORES BLOCKED')).toBeVisible();
  await expect(page.getByText('1 hour / 15 minutes')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Prepare immutable restore plan' })).toBeDisabled();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBeTruthy();
  await page.screenshot({ path: 'artifacts/disaster-recovery-test-mobile.png', fullPage: true });
  expect(errors).toEqual([]);
});
