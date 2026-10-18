const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const { harness } = require('./browser_helpers.cjs');

(async () => harness(8786, async ({ page, idle, errors }) => {
  await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  const audit = async () => {
    const result = await page.evaluate(() => axe.run(document));
    assert.deepEqual(result.violations.map(v => ({ id: v.id, targets: v.nodes.map(n => n.target) })), []);
  };
  const screenshot = async name => {
    const directory = path.resolve(__dirname, '../../artifacts/ui-solid/after');
    fs.mkdirSync(directory, { recursive: true });
    await page.screenshot({ path: path.join(directory, name + '.png'), fullPage: true });
  };
  const fireToast = async (text, warning = false, action = false) => page.evaluate(async ({ text, warning, action }) => {
    const { toast } = await import('/static/src/core/dom.js');
    toast(text, warning, action ? { label: 'Coba tindakan', run: async () => { window.toastActions = (window.toastActions || 0) + 1; } } : null);
  }, { text, warning, action });
  const clearToasts = async () => {
    while (await page.locator('.toast-close').count()) await page.locator('.toast-close').first().click();
  };
  const overflow = async () => assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await screenshot('studio-1440');
  await page.locator('#helpOpen').click();
  assert.equal(await page.locator('#helpDialog').evaluate(el => el.open), true);
  assert.equal(await page.locator('#helpOpen').getAttribute('aria-expanded'), 'true');
  assert.equal(await page.evaluate(() => document.documentElement.style.overflow), 'hidden');
  await audit();
  await screenshot('drawer-1440');
  const dimensions = await page.locator('#helpDialog').boundingBox();
  assert.equal(Math.round(dimensions.x + dimensions.width), 1440);
  for (let i = 0; i < 5; i++) {
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.querySelector('#helpDialog').contains(document.activeElement)), true);
  }
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#helpOpen').evaluate(el => el === document.activeElement), true);
  assert.equal(await page.locator('#helpOpen').getAttribute('aria-expanded'), 'false');
  assert.equal(await page.evaluate(() => document.documentElement.style.overflow), '');
  await page.locator('#helpOpen').click();
  await page.mouse.click(8, 200);
  assert.equal(await page.locator('#helpDialog').evaluate(el => el.open), false);

  for (const width of [320, 390, 768]) {
    await page.setViewportSize({ width, height: 844 });
    await overflow();
    if (width === 390) assert.ok(await page.evaluate(() => document.documentElement.scrollHeight < 1200));
    await page.locator('#helpOpen').click();
    await overflow();
    await audit();
    if (width === 390) await screenshot('drawer-390');
    await page.locator('#helpDialog .dialog-close').click();
    await page.locator('#settingsOpen').click();
    await fireToast('Layanan belum tersedia.', true);
    assert.equal(await page.locator('#settingsDialog .toast').count(), 1);
    await page.locator('#settingsDialog .toast-close').click();
    await audit();
    await overflow();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#settingsOpen').evaluate(el => el === document.activeElement), true);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator('#settingsOpen').click();
  await screenshot('modal-1440');
  await page.keyboard.press('Escape');
  await page.locator('#helpOpen').focus();
  await fireToast('<img src=x onerror=alert(1)>', true, true);
  assert.equal(await page.locator('.toast img').count(), 0);
  await page.locator('.toast-action').focus();
  await page.waitForTimeout(250);
  assert.equal(await page.locator('.toast-action').evaluate(el => el === document.activeElement), true);
  await audit();
  await screenshot('toast-1440');
  await page.locator('.toast-action').click();
  assert.equal(await page.evaluate(() => window.toastActions), 1);
  await page.waitForFunction(() => !document.querySelector('.toast'));
  assert.equal(await page.locator('#helpOpen').evaluate(el => el === document.activeElement), true);
  for (let i = 0; i < 8; i++) await fireToast('Pemberitahuan ' + i, true);
  assert.equal(await page.locator('.toast').count(), 4);
  await clearToasts();

  await page.evaluate(async () => {
    const original = window.setTimeout;
    window.toastTimers = 0;
    window.setTimeout = (handler, timeout, ...args) => {
      if (timeout >= 8990 && timeout <= 9000) window.toastTimers++;
      return original(handler, timeout, ...args);
    };
    const { toast } = await import('/static/src/core/dom.js');
    toast('Pesan dengan tindakan', true, { label: 'Tetap tersedia', run: () => {} });
  });
  assert.equal(await page.evaluate(() => window.toastTimers), 0, 'Action/error messages should not auto-expire');
  await clearToasts();
  await page.locator('#demoStart').click();
  await page.locator('#consentDialog').waitFor({ state: 'visible' });
  await page.mouse.click(8, 200);
  assert.equal(await page.locator('#consentDialog').evaluate(el => el.open), true, 'Consent must not dismiss on backdrop');
  await page.locator('#consentNo').click();
  await idle();
  await page.locator('#newStart').click();
  assert.equal(await page.locator('#resetDialog').evaluate(el => document.activeElement?.getAttribute('data-close')), 'resetDialog', 'Destructive modal starts on cancel');
  await page.mouse.click(8, 200);
  assert.equal(await page.locator('#resetDialog').evaluate(el => el.open), true);
  await page.keyboard.press('Escape');
  assert.equal(await page.evaluate(() => document.documentElement.style.overflow), '');

  const frontend = path.resolve(__dirname, '..');
  const files = ['index.html', ...['src', 'styles'].flatMap(directory => fs.readdirSync(path.join(frontend, directory), { recursive: true }).filter(file => /\.(css|js|html)$/.test(file)).map(file => path.join(directory, file)))];
  for (const file of files) {
    const content = fs.readFileSync(path.join(frontend, file), 'utf8');
    assert.ok(!/(?:linear|radial|conic)-gradient|create(?:Linear|Radial)Gradient|<(?:linear|radial)Gradient/i.test(content), file + ' has a gradient');
    assert.ok(!/\p{Extended_Pictographic}/u.test(content), file + ' has an emoji');
  }
  const css = files.filter(f => f.endsWith('.css')).map(f => fs.readFileSync(path.join(frontend, f), 'utf8')).join('\n');
  const declarations = new Set(Array.from(css.matchAll(/(--[\w-]+)\s*:/g), m => m[1]));
  assert.deepEqual([...new Set(Array.from(css.matchAll(/var\((--[\w-]+)/g), m => m[1]))].filter(v => !declarations.has(v)), []);
  assert.ok(zlib.gzipSync(css).length < 18000);
  assert.deepEqual(errors, []);
  console.log('PASS solid surfaces/SVG icons, drawer keyboard/backdrop/focus, mobile axe/overflow, dismissible bounded toasts, safe modal defaults');
}))().catch(error => { console.error(error); process.exitCode = 1; });
