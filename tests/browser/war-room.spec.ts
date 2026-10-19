import { test, expect } from '@playwright/test';

test('operator explores an incident, approves simulation and exports a report', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (issue) => errors.push(issue.message));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Incident war room' })).toBeVisible();
  await page.screenshot({ path: 'artifacts/war-room-desktop.png', fullPage: true });
  await page.getByRole('button', { name: 'Trigger incident', exact: true }).click();
  await page.getByRole('button', { name: 'Launch scenario' }).click();
  await expect(page.getByRole('button', { name: 'Approve simulated recovery' })).toBeEnabled({
    timeout: 12000,
  });
  await expect(page.getByText('Illustrative sample · not a model measurement')).toBeVisible();
  await page.getByRole('button', { name: 'Approve simulated recovery' }).click();
  await expect(page.getByRole('heading', { name: 'Approve virtual recovery?' })).toBeVisible();
  await page.getByRole('button', { name: 'Approve simulation', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Simulation complete' })).toBeVisible({
    timeout: 12000,
  });
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download incident report' }).click();
  expect((await download).suggestedFilename()).toMatch(/^cloudrescue-.*\.md$/);
  expect(errors).toEqual([]);
});

test('ambiguous evidence escalates and mobile layout has no overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.getByRole('button', { name: 'Trigger incident', exact: true }).click();
  await page.getByRole('radio', { name: /Missing evidence/ }).check();
  await page.getByRole('button', { name: 'Launch scenario' }).click();
  await expect(page.getByRole('button', { name: 'Operator investigation required' })).toBeDisabled({
    timeout: 12000,
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBeTruthy();
  await page.screenshot({ path: 'artifacts/war-room-mobile.png', fullPage: true });
});
