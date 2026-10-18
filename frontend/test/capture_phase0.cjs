const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const stage = process.argv[2] || 'before';
  const root = path.resolve(__dirname, '../..');
  const output = path.join(root, 'artifacts', 'phase-0', stage);
  fs.mkdirSync(output, { recursive: true });
  const base = 'http://127.0.0.1:8767';
  const server = spawn('python', ['-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', '8767'], {
    cwd: root, env: { ...process.env, NINEROUTER_KEY: '' }, stdio: 'ignore',
  });
  let browser;
  try {
    let ready = false;
    for (let i = 0; i < 60; i++) {
      try { if ((await fetch(base + '/api/state')).ok) { ready = true; break; } } catch {}
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    if (!ready) throw new Error('Server not ready');
    browser = await chromium.launch({ headless: true });
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 1000 }, reducedMotion: 'reduce' });
      await page.route('**/api/config', async route => {
        const response = await route.fetch();
        await route.fulfill({ json: { ...await response.json(), ai_available: true } });
      });
      await page.goto(base);
      await page.waitForFunction(() => !document.getElementById('demoStart').disabled);
      await page.check('#aiConsent');
      await page.fill('#aiText', 'depan diutamakan hitam, 160×120 mm, boleh satu sambungan');
      await page.click('#askAi');
      await page.locator('#aiProposal .callout').waitFor();
      await page.locator('nav.tabs').evaluate(el => { el.style.position = 'relative'; });
      await page.locator('.ai-primary').screenshot({ path: path.join(output, `ai-failure-${width}.png`) });
      await page.locator('nav.tabs').evaluate(el => { el.style.position = ''; });
      await page.screenshot({ path: path.join(output, `screen-${width}.png`), fullPage: true });
      await page.close();
    }
    console.log('Saved ' + stage + ' screenshots at 1440 and 390 px');
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
