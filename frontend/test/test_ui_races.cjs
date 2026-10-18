const assert = require('node:assert/strict');
const { harness } = require('./browser_helpers.cjs');

const PORT = 8787;
const VIEWPORT = { width: 1440, height: 1000 };

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function deadline(promise, label, timeout = 15000) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(label)), timeout); }),
    ]);
  } finally { clearTimeout(timer); }
}

// Fetch first, then hold delivery: the barrier proves the backend has already
// accepted the mutation, rather than merely delaying a request before it runs.
async function holdResponse(page, pattern) {
  const accepted = deferred(), gate = deferred(), delivered = deferred();
  const calls = [], routeErrors = [];
  const handler = async route => {
    const first = calls.length === 0;
    calls.push(route.request().postDataJSON());
    try {
      const response = await route.fetch();
      if (first) {
        accepted.resolve({ status: response.status(), data: await response.json() });
        await gate.promise;
      }
      await route.fulfill({ response });
    } catch (error) {
      routeErrors.push(String(error));
      if (first) accepted.reject(error);
    } finally {
      if (first) delivered.resolve();
    }
  };
  await page.route(pattern, handler);
  return {
    calls,
    accepted: () => deadline(accepted.promise, 'The backend never accepted ' + pattern),
    release: () => gate.resolve(),
    delivered: () => deadline(delivered.promise, 'The held response was not delivered: ' + pattern),
    async dispose() {
      gate.resolve();
      if (calls.length) await deadline(delivered.promise, 'Route cleanup timed out: ' + pattern);
      await page.unroute(pattern, handler);
      assert.deepEqual(routeErrors, [], 'Unexpected route failures for ' + pattern);
    },
  };
}

(async () => harness(PORT, async ({ base, browser, errors }) => {
  const failures = [];
  const runtimeErrors = [];

  async function scenario(name, run) {
    const context = await browser.newContext({ viewport: VIEWPORT, reducedMotion: 'reduce' });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.on('pageerror', error => runtimeErrors.push(name + ': ' + String(error)));
    const idle = () => page.waitForFunction(() => !document.getElementById('demoStart').disabled);
    const state = async () => (await context.request.get(base + '/api/state')).json();
    const projects = async () => (await context.request.get(base + '/api/projects')).json();

    async function dimensions() {
      if (!(await page.locator('#step2').isVisible())) await page.locator('[data-step="2"]').click();
      if (!(await page.locator('.manual-fields').evaluate(node => node.open))) {
        await page.locator('.manual-fields summary').click();
      }
    }

    async function savedWidth(width) {
      await page.waitForFunction(() => {
        const status = document.getElementById('saveStatus').textContent;
        return !document.getElementById('sessionConflict').hidden || !status.startsWith('Saving your details');
      });
      assert.equal(await page.locator('#sessionConflict').isVisible(), false,
        'Editing after an accepted response must not produce a tab-conflict blocker');
      assert.match(await page.locator('#saveStatus').textContent(), /Autosaved/,
        'The latest dimensions should finish autosaving');
      assert.equal(await page.locator('#widthCm').inputValue(), String(width),
        'The visible width must retain the newer edit');
      assert.equal((await state()).data.request.W_mm, width * 10,
        'Autosave must persist the visible width, not the held response snapshot');
    }

    async function demo() {
      await page.locator('#demoStart').click();
      await page.locator('#consentNo').click();
      await page.locator('#step3').waitFor({ state: 'visible' });
      await idle();
      assert.match(await page.locator('#metrics').textContent(), /16 × 12 cm/);
    }

    async function planWidth(width) {
      await page.locator('#confirmFabric').click();
      await page.locator('#step3').waitFor({ state: 'visible' });
      await idle();
      assert.match(await page.locator('#metrics').textContent(), new RegExp(width + ' × 12 cm'),
        'A newly generated design must use the surviving dimensions');
    }

    async function saveProject(name) {
      await page.locator('#projectSave').click();
      await page.locator('#projectName').fill(name);
      await page.locator('#saveProjectForm button[type="submit"]').click();
      await page.locator('#saveProjectDialog').waitFor({ state: 'hidden' });
      await idle();
    }

    async function reset() {
      await page.locator('#newStart').click();
      await page.locator('#resetYes').click();
      await page.locator('#resetDialog').waitFor({ state: 'hidden' });
      await page.locator('#step1').waitFor({ state: 'visible' });
      await idle();
      await page.locator('#toasts .toast-action').waitFor({ state: 'visible' });
    }

    async function clickUndo() {
      await page.locator('#toasts .toast-action').click();
      await page.locator('#toasts .toast-action').waitFor({ state: 'hidden' });
      await idle();
    }

    async function listProjects() {
      await page.locator('#projectOpen').click();
      await page.locator('#projectList .project-item').first().waitFor();
    }

    try {
      await page.goto(base + '/?test=1');
      await idle();
      await run({ page, context, idle, state, projects, dimensions, savedWidth, demo, planWidth,
        saveProject, reset, clickUndo, listProjects });
      console.log('PASS ' + name);
    } catch (error) {
      failures.push(name);
      console.error('FAIL ' + name + '\n' + (error.stack || error));
    } finally { await context.close(); }
  }

  await scenario('accepted save response cannot replace dimensions edited after Escape',
    async ({ page, idle, projects, dimensions, savedWidth, demo, planWidth }) => {
      await demo();
      const held = await holdResponse(page, '**/api/projects/save');
      try {
        await page.locator('#projectSave').click();
        await page.locator('#projectName').fill('Simpanan sebelum edit');
        await page.locator('#saveProjectForm button[type="submit"]').click();
        const accepted = await held.accepted();
        assert.equal(accepted.status, 200);
        assert.equal(accepted.data.state.data.request.W_mm, 160);
        assert.equal((await projects()).projects.length, 1, 'The held save really reached storage');
        await page.keyboard.press('Escape');
        await page.locator('#saveProjectDialog').waitFor({ state: 'hidden' });
        await dimensions();
        await page.locator('#widthCm').fill('15');
        held.release();
        await held.delivered();
        await idle();
        await savedWidth(15);
        assert.equal(await page.locator('#step2').isVisible(), true);
        assert.equal(await page.locator('#toGuide').isDisabled(), true,
          'The old saved design must not become downloadable after the edit');
        await planWidth(15);
      } finally { await held.dispose(); }
    });

  for (const accept of [true, false]) {
    await scenario('accepted split ' + (accept ? 'acceptance' : 'rejection') + ' cannot overwrite a dimension edit',
      async ({ page, idle, state, dimensions, savedWidth, demo, planWidth }) => {
        await demo();
        await page.locator('[data-step="2"]').click();
        await page.locator('[data-available="0"]').click();
        await page.locator('#confirmFabric').click();
        await page.locator('#acceptSplit').waitFor({ state: 'visible' });
        await idle();
        assert.equal((await state()).candidate.variant, 'front_split_center');
        const held = await holdResponse(page, '**/api/accept-split');
        try {
          await page.locator(accept ? '#acceptSplit' : '#rejectSplit').click();
          const accepted = await held.accepted();
          assert.equal(accepted.status, 200);
          assert.equal(held.calls[0].accepted, accept);
          assert.equal(Boolean(accepted.data.active_revision), accept,
            'The backend must finish the requested split decision before the dimension edit');
          if (!accept) assert.equal(accepted.data.candidate, null);
          await dimensions();
          await page.locator('#widthCm').fill('15');
          held.release();
          await held.delivered();
          await idle();
          await savedWidth(15);
          assert.equal(await page.locator('#step2').isVisible(), true,
            'The delayed split decision must not navigate away from the newer dimensions');
          assert.equal(await page.locator('#toGuide').isDisabled(), true);
          assert.equal(await page.locator('#downloadGuide').isDisabled(), true);
          assert.equal((await state()).active_revision, null);
          assert.equal((await state()).candidate, null);
          await planWidth(15);
        } finally { await held.dispose(); }
      });
  }

  await scenario('reset Undo is invalid after a newer local edit',
    async ({ page, state, dimensions, savedWidth, reset, clickUndo }) => {
      await page.locator('#manualStart').click();
      await dimensions();
      await page.locator('#widthCm').fill('14');
      await savedWidth(14);
      await reset();
      await page.locator('#manualStart').click();
      await dimensions();
      const held = await holdResponse(page, '**/api/cancel');
      try {
        await page.locator('#widthCm').fill('15');
        assert.equal((await held.accepted()).status, 200);
        // The browser still has the reset token while cancellation is held.
        // Undo must be invalidated by the local edit itself, not just by a
        // subsequently adopted generation token.
        await clickUndo();
        assert.equal(await page.locator('#widthCm').inputValue(), '15');
        assert.equal(await page.locator('#step2').isVisible(), true);
        await page.locator('.toast.warning .toast-message').filter({ hasText: 'Inputs already changed' }).waitFor();
        held.release();
        await held.delivered();
        await savedWidth(15);
        assert.equal((await state()).active_revision, null);
      } finally { await held.dispose(); }
    });

  await scenario('opening a named project focuses its heading and invalidates reset Undo',
    async ({ page, idle, state, dimensions, savedWidth, demo, saveProject, reset, clickUndo, listProjects }) => {
      await demo();
      await saveProject('Tujuan rancangan tersimpan');
      await dimensions();
      await page.locator('#widthCm').fill('14');
      await savedWidth(14);
      await reset();
      await listProjects();
      await page.getByRole('button', { name: 'Open Tujuan rancangan tersimpan', exact: true }).click();
      await page.locator('#openProjectDialog').waitFor({ state: 'hidden' });
      await page.locator('#step3').waitFor({ state: 'visible' });
      await idle();
      assert.equal(await page.evaluate(() => document.activeElement.id), 'designTitle',
        'Closing the picker must leave focus on the destination heading, not its launcher');
      assert.match(await page.locator('#metrics').textContent(), /16 × 12 cm/);
      await clickUndo();
      assert.equal(await page.locator('#step3').isVisible(), true,
        'Undo from the previous reset must not replace or leave the opened project');
      assert.equal(await page.locator('#widthCm').inputValue(), '16');
      assert.equal(await page.locator('#toGuide').isDisabled(), false);
      assert.match(await page.locator('#metrics').textContent(), /16 × 12 cm/);
      await page.locator('.toast.warning .toast-message').filter({ hasText: 'Inputs already changed' }).waitFor();
      const opened = await state();
      assert.equal(opened.data.request.W_mm, 160);
      assert.equal(opened.active_revision.validation_report.passed, true);
    });

  await scenario('double delete while its accepted response is held deletes only once',
    async ({ page, projects, saveProject, listProjects }) => {
      await page.locator('#manualStart').click();
      await saveProject('Hapus sekali');
      await saveProject('Tetap tersimpan');
      await listProjects();
      await page.getByRole('button', { name: 'Delete saved project Hapus sekali', exact: true }).click();
      const held = await holdResponse(page, '**/api/projects/delete');
      try {
        await page.locator('#deleteProjectYes').dblclick();
        const accepted = await held.accepted();
        assert.equal(accepted.status, 200);
        assert.equal(accepted.data.deleted, true);
        assert.equal(await page.locator('#deleteProjectYes').isDisabled(), true,
          'Confirmation must be disabled while its delete is pending');
        held.release();
        await held.delivered();
        await page.locator('#deleteProjectDialog').waitFor({ state: 'hidden' });
        await page.getByRole('button', { name: 'Open Tetap tersimpan', exact: true }).waitFor();
        assert.equal(await page.getByRole('button', { name: 'Open Hapus sekali', exact: true }).count(), 0);
        assert.equal(held.calls.length, 1, 'A real double click must issue only one delete');
        assert.deepEqual((await projects()).projects.map(project => project.name), ['Tetap tersimpan']);
        assert.equal(await page.locator('.toast.warning').count(), 0,
          'A duplicate delete must not produce a project-not-found warning');
      } finally { await held.dispose(); }
    });

  await scenario('late delete completion cannot close another project confirmation',
    async ({ page, projects, saveProject, listProjects }) => {
      await page.locator('#manualStart').click();
      await saveProject('Target pertama');
      await saveProject('Target kedua');
      await listProjects();
      const before = (await projects()).projects;
      const first = before.find(project => project.name === 'Target pertama');
      const second = before.find(project => project.name === 'Target kedua');
      const held = await holdResponse(page, '**/api/projects/delete');
      try {
        await page.getByRole('button', { name: 'Delete saved project Target pertama', exact: true }).click();
        await page.locator('#deleteProjectYes').click();
        assert.equal((await held.accepted()).status, 200);
        await page.keyboard.press('Escape');
        await page.locator('#deleteProjectDialog').waitFor({ state: 'hidden' });
        await page.getByRole('button', { name: 'Delete saved project Target kedua', exact: true }).click();
        assert.equal(await page.locator('#deleteProjectName').textContent(), 'Target kedua');
        held.release();
        await held.delivered();
        await page.locator('#deleteProjectYes').waitFor({ state: 'visible' });
        await page.waitForFunction(() => !document.getElementById('deleteProjectYes').disabled);
        await page.locator('.toast-message').filter({ hasText: 'Saved project “Target pertama” deleted.' }).waitFor();
        assert.equal(await page.locator('#deleteProjectDialog').isVisible(), true,
          'Completing the first delete must not dismiss the second target confirmation');
        assert.equal(await page.locator('#deleteProjectName').textContent(), 'Target kedua');
        assert.deepEqual(held.calls.map(call => call.project_id), [first.id]);
        assert.deepEqual((await projects()).projects.map(project => project.name), ['Target kedua']);
        await page.locator('#deleteProjectYes').click();
        await page.locator('#deleteProjectDialog').waitFor({ state: 'hidden' });
        await page.getByText('No saved projects yet.', { exact: false }).waitFor();
        assert.deepEqual(held.calls.map(call => call.project_id), [first.id, second.id]);
        assert.equal((await projects()).projects.length, 0);
      } finally { await held.dispose(); }
    });

  assert.deepEqual(errors, []);
  assert.deepEqual(runtimeErrors, [], 'No scenario should emit browser runtime errors');
  assert.equal(failures.length, 0, 'UI race regressions failed: ' + failures.join('; '));
  console.log('PASS all 7 UI race regression scenarios (no screenshot artifacts)');
}))().catch(error => { console.error(error); process.exitCode = 1; });
