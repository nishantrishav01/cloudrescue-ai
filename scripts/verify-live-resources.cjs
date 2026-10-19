const { chromium } = require('@playwright/test');

async function main() {
  let input = '';
  for await (const chunk of process.stdin) input += chunk;
  const { token, site, expectedResources } = JSON.parse(input);
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.addInitScript(value => {
      sessionStorage.setItem('access_token', value);
      sessionStorage.setItem('expires_at', String(Date.now() + 300000));
    }, token);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', issue => errors.push(issue.message));
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(site);
    await page.getByRole('button', { name: 'Live resources', exact: true }).click();
    await page.locator('.live-resource').first().waitFor({ timeout: 30000 });
    const resources = await page.locator('.live-resource').count();
    if (resources !== expectedResources) throw new Error(`Expected ${expectedResources} live resources; found ${resources}.`);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: 'artifacts/live-resources-deployed.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)) throw new Error('Live inventory has mobile overflow.');
    await page.screenshot({ path: 'artifacts/live-resources-deployed-mobile.png', fullPage: true });
    await page.getByRole('button', { name: 'Toggle menu' }).click();
    await page.getByRole('button', { name: 'Disaster recovery', exact: true }).click();
    await page.getByText('1 hour / 15 minutes', { exact: true }).waitFor({ timeout: 30000 });
    await page.getByText('REAL RESTORES BLOCKED', { exact: true }).waitFor();
    if (await page.getByRole('button', { name: 'Prepare immutable restore plan' }).isEnabled()) throw new Error('An unconfigured restore was enabled.');
    if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)) throw new Error('Recovery configuration has mobile overflow.');
    await page.screenshot({ path: 'artifacts/recovery-deployed-mobile.png', fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({ path: 'artifacts/recovery-deployed.png', fullPage: true });
    if (errors.length) throw new Error(errors.join('; '));
    console.log(JSON.stringify({ live_ui: 'passed', resources, mobile_overflow: false, browser_errors: errors, real_restores: 'blocked' }));
  } finally {
    await browser.close();
  }
}

main().catch(issue => { console.error(issue.message); process.exitCode = 1; });
