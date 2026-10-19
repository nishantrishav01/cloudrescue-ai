const { chromium } = require('@playwright/test');

async function main() {
  let input = '';
  for await (const chunk of process.stdin) input += chunk;
  const { token, site } = JSON.parse(input);
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
    await context.addInitScript(value => {
      sessionStorage.setItem('access_token', value);
      sessionStorage.setItem('expires_at', String(Date.now() + 300000));
    }, token);
    const page = await context.newPage();
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const errors = [];
    page.on('pageerror', issue => errors.push(issue.message));
    await page.goto(site);
    await page.getByRole('button', { name: 'Benchmarks', exact: true }).click();
    await page.getByRole('heading', { name: 'Labeled evaluation · 30 cases' }).waitFor({ timeout: 30000 });
    const rows = await page.locator('.benchmark-table tbody tr').count();
    if (rows !== 30 || errors.length) throw new Error(`Benchmark UI check failed: rows=${rows}, errors=${errors.join('; ')}`);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: 'artifacts/benchmark-live.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    if (overflow) throw new Error('Benchmark page overflows the mobile viewport.');
    await page.screenshot({ path: 'artifacts/benchmark-live-mobile.png', fullPage: true });
    console.log(JSON.stringify({ benchmark_ui: 'passed', rows, mobile_overflow: overflow, browser_errors: errors }));
  } finally {
    await browser.close();
  }
}

main().catch(issue => { console.error(issue.message); process.exitCode = 1; });
