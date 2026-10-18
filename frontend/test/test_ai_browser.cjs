const assert=require('node:assert/strict');

const {harness}=require('./browser_helpers.cjs');

const event=(name,data)=>`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

const pixel=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');

(async()=>harness(8766,async({page,context,base,idle,errors,shot})=>{

  let configMode='error';await page.route('**/api/config',async route=>{if(configMode==='error')return route.fulfill({status:503,json:{detail:{code:'AI_UNAVAILABLE'}}});const response=await route.fetch();const config={...await response.json(),ai_available:true,ai_image_available:configMode==='ready'};await route.fulfill({json:config});});

  await page.reload();await page.locator('.toast-action').waitFor();await page.locator('#storyInput').fill('Pouch 14×10 cm, depan biru.');configMode='outdated';await page.locator('.toast-action').click();await idle();assert.equal(await page.locator('#storyInput').inputValue(),'Pouch 14×10 cm, depan biru.');

  await page.locator('#settingsOpen').click();configMode='ready';await page.locator('#refreshAiConfig').click();await idle();await page.locator('[data-close="settingsDialog"]').click();assert.match(await page.locator('#aiStatus').textContent(),/Design assistant/);

  await page.route('**/api/ai/chat',async route=>{const s=await (await context.request.get(base+'/api/state')).json();const proposal=structuredClone(s.data.request);proposal.W_mm=140;proposal.H_mm=100;proposal.confirmed=false;const revision=route.request().postDataJSON().replan;if(revision){proposal.preferences.front_color='krem';s.data.request=proposal;s.data.materials[3].outer_mm=[[0,0],[200,0],[200,170],[0,170]];await context.request.post(base+'/api/input',{data:{data:s.data}});await context.request.post(base+'/api/confirm',{data:{}});const response=await context.request.post(base+'/api/plan',{data:{use_ai:false}});const planned=await response.json();await route.fulfill({contentType:'text/event-stream',body:event('brief',{status:'proposal',proposal,reply:'Warna depan kamu diperbarui.',understood:[],assumptions:[],questions:[],warnings:[],trace:{mode:'live',live_model_used:true}})+event('design_ready',planned)+event('done',{ok:true})});return;}await route.fulfill({contentType:'text/event-stream',body:event('brief',{status:'proposal',proposal,reply:'Aku menangkap kebutuhan kamu.',understood:[],assumptions:[],questions:[],warnings:[],trace:{mode:'live',live_model_used:true}})+event('done',{ok:true})});});

  await page.route('**/api/plan',async route=>{const response=await route.fetch({postData:JSON.stringify({...route.request().postDataJSON(),use_ai:false})});const s=await response.json();s.active_revision.ai_package={summary:'Pouch untuk kamu.',fabric_choice:'<script>throw new Error("unsafe")</script>',what_to_expect:'Panel utuh.',watch_out:'Periksa kain.',limits:'Ilustrasi.'};s.ai_trace={live_model_used:true};await route.fulfill({json:s});});

  let hold=false,release,entered=false,cancelled=0;

  await page.route('**/api/ai/image/cancel',async route=>{cancelled++;await route.fulfill({json:{cancelled:true}});});

  await page.route('**/api/ai/image',async route=>{entered=true;const s=await (await context.request.get(base+'/api/state')).json();if(hold)await new Promise(r=>release=r);await route.fulfill({json:{source:'live',media_type:'image/png',input_signature:s.signature,revision_id:s.active_revision.design_revision_id,image_id:'mock',color_warning:'Warna ilustrasi berbeda dari swatch kain.'}}).catch(()=>{});});

  await page.route('**/api/ai/image?**',route=>route.fulfill({contentType:'image/png',body:pixel}));

  await page.locator('#sendStory').click();await page.locator('#consentYes').click();await page.locator('#useBrief').waitFor();await idle();await page.locator('#useBrief').click();await page.locator('#step3').waitFor({state:'visible'});await page.locator('#imagePreview img').waitFor();await idle();assert.match(await page.locator('#designSource').textContent(),/AI-designed/);assert.equal(await page.locator('#fabricChoice script').count(),0);assert.match(await page.locator('#fabricChoice').textContent(),/<script>/);assert.match(await page.locator('#imageStatus').textContent(),/differ/);await shot('ai-rancangan-1440',2);

  await page.locator('#reviseDesign').click();await page.locator('#storyInput').fill('ganti warna depan jadi krem');await page.locator('#sendStory').click();await page.locator('#step3').waitFor({state:'visible'});await page.waitForFunction(()=>window.__pouch3d?.getState().frontHex[0]==='#D9C7A5');await page.locator('#imagePreview img').waitFor();await idle();assert.match(await page.locator('#designDiff').textContent(),/denim blue.*beige/);assert.equal(await page.locator('#metrics .metric b').first().textContent(),'14 × 10 cm');await shot('revisi-krem-1440',2);await page.setViewportSize({width:390,height:844});await shot('revisi-krem-390',2);await page.setViewportSize({width:1440,height:1000});

  hold=true;entered=false;await page.locator('#regenerateImage').click();for(let i=0;i<50&&!entered;i++)await page.waitForTimeout(20);assert.equal(entered,true);await page.locator('#cancelImage').click();release();await page.waitForTimeout(200);assert.equal(cancelled,1);assert.match(await page.locator('#imageStatus').textContent(),/cancelled/);

  hold=true;entered=false;await page.locator('#regenerateImage').click();for(let i=0;i<50&&!entered;i++)await page.waitForTimeout(20);await page.locator('[data-step="2"]').click();await page.locator('.manual-fields summary').click();await page.locator('#widthCm').fill('13');release();await page.waitForTimeout(200);assert.equal(await page.locator('#imagePreview img').count(),0);assert.equal(await page.locator('#downloadGuide').isDisabled(),true);assert.deepEqual(errors,[]);

  console.log('PASS configuration recovery, remembered text, one consent, checked AI plan, escaped notes, image warning, cancellation and late image');

}))().catch(e=>{console.error(e);process.exitCode=1;});

