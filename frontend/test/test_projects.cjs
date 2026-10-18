const assert = require('node:assert/strict');
const {harness} = require('./browser_helpers.cjs');
const path = require('node:path');
const fs = require('node:fs');

(async () => harness(8774, async ({base,browser,context,page,idle,errors,shot}) => {
  const state = async () => (await context.request.get(base + '/api/state')).json();
  const waitWidth = async width => {
    for(let i=0;i<60;i++) {
      if((await state()).data.request.W_mm === width) return;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.fail('Autosave did not reach width ' + width);
  };
  const audit = async target => {
    await target.addScriptTag({path:require.resolve('axe-core/axe.min.js')});
    const result = await target.evaluate(() => axe.run(document));
    assert.deepEqual(result.violations.map(item => ({id:item.id,targets:item.nodes.map(node=>node.target)})),[]);
  };
  await shot('cerita-1440',5);
  await page.setViewportSize({width:390,height:844});
  await shot('cerita-390',5);
  const storyHeight = await page.evaluate(() => document.documentElement.scrollHeight);
  assert.ok(storyHeight < 1200, 'Mobile story height ' + storyHeight);
  await audit(page);
  await page.setViewportSize({width:1440,height:1000});
  assert.equal((await state()).telemetry_enabled,false);
  await page.locator('#settingsOpen').click();
  await page.locator('#activityEnabled').check();
  await page.waitForFunction(async () => (await (await fetch('/api/state')).json()).telemetry_enabled);
  await shot('pengaturan-1440',5);
  await audit(page);
  await page.setViewportSize({width:390,height:844});await shot('pengaturan-390',5);
  await page.locator('#activityEnabled').uncheck();
  await page.waitForFunction(async () => !(await (await fetch('/api/state')).json()).telemetry_enabled);
  await page.locator('[data-close="settingsDialog"]').click();
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#demoStart').click();await page.locator('#consentNo').click();
  await page.locator('#step3').waitFor({state:'visible'});await idle();
  const initial = await state();
  assert.equal(initial.active_revision.validation_report.passed,true);
  await page.locator('.explanation-card summary').click();
  assert.equal(await page.locator('#planExplanation h3').count(),5);
  await page.locator('.explanation-card summary').click();
  await page.locator('#projectSave').click();
  await page.locator('#projectName').fill('Pouch biru untuk dibuka lagi');
  await shot('simpan-1440',5);await audit(page);
  await page.setViewportSize({width:390,height:844});await shot('simpan-390',5);
  await page.locator('#saveProjectForm button[type="submit"]').click();
  await page.locator('#saveProjectDialog').waitFor({state:'hidden'});await idle();
  const list = await (await context.request.get(base + '/api/projects')).json();
  assert.equal(list.projects.length,1);assert.equal(list.projects[0].name,'Pouch biru untuk dibuka lagi');
  await page.reload();await idle();
  await page.locator('#step3').waitFor({state:'visible'});
  assert.equal((await state()).active_revision.design_revision_id,initial.active_revision.design_revision_id);
  await shot('rancangan-390',5);
  const designHeight = await page.evaluate(() => document.documentElement.scrollHeight);
  // Include page header, footer and the fixed download action in the bound.
  assert.ok(designHeight <= 2 * 844, 'Mobile design height ' + designHeight);
  await page.setViewportSize({width:1440,height:1000});await shot('rancangan-1440',5);
  await page.locator('[data-step="2"]').click();
  await page.locator('.manual-fields summary').click();
  await page.locator('#widthCm').fill('14');await waitWidth(140);
  assert.equal((await state()).active_revision,null);
  await page.reload();await idle();
  await page.locator('#step2').waitFor({state:'visible'});
  assert.equal(await page.locator('#widthCm').inputValue(),'14');
  assert.match(await page.locator('#saveStatus').textContent(),/Autosaved/);
  await page.locator('#projectOpen').click();
  await page.locator('#projectList .project-item > button').first().waitFor();
  await shot('buka-1440',5);await audit(page);
  await page.setViewportSize({width:390,height:844});await shot('buka-390',5);
  await page.locator('#projectList .project-item > button').first().click();await idle();
  const opened = await state();
  assert.equal(opened.data.request.W_mm,160);assert.equal(opened.active_revision.validation_report.passed,true);
  // Opening a saved immutable design preserves its ID and rebases its job guards.
  assert.ok(opened.generation_token > initial.generation_token);
  assert.notEqual(opened.job_token,initial.job_token);
  assert.equal(await page.locator('#toGuide').isDisabled(),false);
  await page.setViewportSize({width:1440,height:1000});
  const other = await context.newPage();await other.goto(base + '/?test=1');
  await other.waitForFunction(() => !document.getElementById('demoStart').disabled);
  await other.locator('[data-step="2"]').click();await other.locator('.manual-fields summary').click();
  await other.locator('#widthCm').fill('13');await waitWidth(130);
  await page.locator('#sessionConflict').waitFor({state:'visible'});
  assert.equal(await page.locator('#step3').evaluate(node => node.inert),true);
  assert.equal(await page.locator('#toGuide').isDisabled(),true);
  await shot('tab-berubah-1440',5);await audit(page);
  await page.setViewportSize({width:390,height:844});await shot('tab-berubah-390',5);
  await page.locator('#loadOtherTab').click();await idle();
  assert.equal(await page.locator('#widthCm').inputValue(),'13');
  assert.equal(await page.locator('#step3').evaluate(node => node.inert),false);
  await page.locator('.manual-fields summary').click();await page.locator('#widthCm').fill('12');await waitWidth(120);
  await other.locator('#sessionConflict').waitFor({state:'visible'});
  await other.locator('#keepThisTab').click();
  await waitWidth(130);
  await other.close();
  if(await page.locator('#sessionConflict').isVisible()) { await page.locator('#loadOtherTab').click(); await idle(); }

  // Hold an accepted input response, then type again. The following cancellation
  // must wait for that input to update the browser's guard before it is sent.
  let releaseInput, enteredInput;
  const inputEntered = new Promise(resolve => enteredInput=resolve), inputGate = new Promise(resolve => releaseInput=resolve);
  let holdInput = true;
  await page.route('**/api/input', async route => {
    const response = await route.fetch();
    if(holdInput) { holdInput=false; enteredInput(); await inputGate; }
    await route.fulfill({response}).catch(()=>{});
  });
  await page.locator('[data-step="2"]').click();
  if(!(await page.locator('.manual-fields').evaluate(node=>node.open))) await page.locator('.manual-fields summary').click();
  await page.locator('#widthCm').fill('14');await inputEntered;
  await page.locator('#widthCm').fill('15');
  assert.equal(await page.locator('#sessionConflict').isVisible(),false);
  releaseInput();await waitWidth(150);
  await page.waitForFunction(()=>document.getElementById('saveStatus').textContent.startsWith('Autosaved'));
  assert.equal(await page.locator('#sessionConflict').isVisible(),false);
  await page.unroute('**/api/input');

  // A reload before the debounce finishes still offers the latest measured input.
  let releaseCancel, enteredCancel;
  const cancelEntered = new Promise(resolve=>enteredCancel=resolve), cancelGate = new Promise(resolve=>releaseCancel=resolve);
  let holdCancel = true;
  await page.route('**/api/cancel', async route => {
    const response = await route.fetch();
    if(holdCancel) {holdCancel=false;enteredCancel();await cancelGate;}
    await route.fulfill({response}).catch(()=>{});
  });
  await page.locator('#widthCm').fill('12');await cancelEntered;
  await page.reload();releaseCancel();
  await page.waitForFunction(()=>!document.getElementById('sessionConflict').hidden || !document.getElementById('demoStart').disabled);
  if(await page.locator('#sessionConflict').isVisible()) {
    assert.match(await page.locator('#conflictTitle').textContent(),/unsaved details/);
    await shot('pulihkan-isian-390',5);await audit(page);
    await page.locator('#keepThisTab').click();
  }
  await waitWidth(120);await idle();
  assert.equal(await page.locator('#widthCm').inputValue(),'12');
  await page.unroute('**/api/cancel');

  await page.locator('#confirmFabric').click();await page.locator('#step3').waitFor({state:'visible'});await idle();
  const beforeReplan = await state();
  let downloads = 0;page.on('download',()=>downloads++);
  let changedDuringExport = false;
  await page.route('**/api/export-pdf?**', async route => {
    if(!changedDuringExport) {
      changedDuringExport=true;
      const response=await context.request.post(base+'/api/plan',{data:{use_ai:false,expected_generation_token:beforeReplan.generation_token}});
      assert.equal(response.status(),200);
      const next=await response.json();
      assert.equal(next.generation_token,beforeReplan.generation_token);
      assert.equal(next.signature,beforeReplan.signature);
      assert.notEqual(next.active_revision.design_revision_id,beforeReplan.active_revision.design_revision_id);
    }
    const response=await route.fetch();await route.fulfill({response});
  });
  await page.locator('#toGuide').click();
  await page.locator('#sessionConflict').waitFor({state:'visible'});
  assert.equal(downloads,0,'A PDF from another displayed design must not download');
  assert.equal(await page.locator('#pdfPreview iframe').count(),0);
  await page.unroute('**/api/export-pdf?**');
  await page.locator('#loadOtherTab').click();await idle();

  // Registered explanations require no model and leave the checked plan intact.
  await page.locator('#settingsOpen').click();await page.locator('#aiEnabled').check();await page.locator('[data-close="settingsDialog"]').click();
  const beforeExplanation = await state();
  await page.locator('[data-step="1"]').click();await page.locator('#storyInput').fill('Jelaskan langkah 4');await page.locator('#sendStory').click();await idle();
  assert.equal((await state()).active_revision.design_revision_id,beforeExplanation.active_revision.design_revision_id);
  assert.match(await page.locator('#messages .bubble').last().textContent(),/Periksa sudut.*pertemuan garis/);
  assert.equal(await page.locator('#sessionConflict').isVisible(),false);
  const image = {source:'cached',media_type:'image/png',input_signature:beforeExplanation.signature,revision_id:beforeExplanation.active_revision.design_revision_id,image_id:'browser-action-fixture'};
  const event=(name,value)=>'event: '+name+'\ndata: '+JSON.stringify(value)+'\n\n';
  const pixel=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
  await page.route('**/api/ai/chat',route=>route.fulfill({contentType:'text/event-stream',body:event('image',image)+event('message',{text:'Ilustrasi diperbarui. Pola tetap sama.'})+event('done',{ok:true})}));
  await page.route('**/api/ai/image?**',route=>route.fulfill({contentType:'image/png',body:pixel}));
  await page.locator('#storyInput').fill('Buat ulang gambar');await page.locator('#sendStory').click();await page.locator('#imagePreview img').waitFor();await idle();
  assert.equal((await state()).active_revision.design_revision_id,beforeExplanation.active_revision.design_revision_id);
  await page.unroute('**/api/ai/chat');
  let imagePosts = 0;
  page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/api/ai/image'))imagePosts++;});
  await page.route('**/api/state',async route=>{const response=await route.fetch();await route.fulfill({json:{...await response.json(),image}});});
  await page.reload();await idle();
  await page.waitForFunction(()=>document.querySelector('#imagePreview img')?.naturalWidth===1);
  assert.equal(imagePosts,0,'Reload uses the saved illustration rather than regenerating it');
  await page.unroute('**/api/state');await page.unroute('**/api/ai/image?**');
  await page.locator('#projectOpen').click();await page.locator('#projectList .text-button').click();
  await page.locator('#deleteProjectDialog').waitFor({state:'visible'});await audit(page);
  await page.locator('#deleteProjectYes').click();
  await page.waitForFunction(()=>document.getElementById('projectList').textContent.includes('No saved projects yet.'));
  assert.equal((await (await context.request.get(base+'/api/projects')).json()).projects.length,0);
  assert.equal((await state()).data.request.W_mm,120);
  await page.locator('[data-close="openProjectDialog"]').click();

  // A separate browser cookie owns a separate session and project list.
  const stranger = await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'});
  const isolated = await (await stranger.request.get(base + '/api/state')).json();
  assert.equal(isolated.data.request.W_mm,160);assert.equal(isolated.active_revision,null);
  assert.equal((await (await stranger.request.get(base + '/api/projects')).json()).projects.length,0);
  const firstUse = await stranger.newPage();await firstUse.goto(base+'/');
  await firstUse.locator('#microTour').waitFor({state:'visible'});
  const firstHeight=await firstUse.evaluate(()=>document.documentElement.scrollHeight);
  assert.ok(firstHeight<1200,'First-use mobile story height '+firstHeight);
  await audit(firstUse);
  const firstDir=path.resolve(__dirname,'../../artifacts/phase-5/after');
  await firstUse.screenshot({path:path.join(firstDir,'premiere-390.png'),fullPage:true});
  await firstUse.setViewportSize({width:1440,height:1000});await firstUse.screenshot({path:path.join(firstDir,'premiere-1440.png'),fullPage:true});
  for(const count of ['1','2','3']) {
    assert.equal(await firstUse.locator('#tourCount').textContent(),count+' of 3');
    await firstUse.locator('#tourNext').focus();await firstUse.keyboard.press('Enter');
  }
  assert.equal(await firstUse.locator('#microTour').isVisible(),false);
  await firstUse.reload();assert.equal(await firstUse.locator('#microTour').isVisible(),false);
  await stranger.close();
  assert.deepEqual(errors,[]);
  const evidence = {storyHeight,designHeight,firstHeight,autosave:true,reload:true,namedOpen:true,tabConflict:true,telemetryOptIn:true,a11y:true,inputQueue:true,rapidReloadRecovery:true,sameInputReplanExportGuard:true,registeredChatActions:true,savedImageDisplay:true,deleteSavedProject:true,firstUseTour:true};
  fs.mkdirSync(path.resolve(__dirname,'../../artifacts/phase-5'),{recursive:true});
  fs.writeFileSync(path.resolve(__dirname,'../../artifacts/phase-5/frontend-checks.json'),JSON.stringify(evidence,null,2));
  console.log('PASS autosave/reload, Save/Open, isolated browser sessions, tab conflicts, opt-in events, five notes and axe');
}))().catch(error => {console.error(error);process.exitCode=1;});
