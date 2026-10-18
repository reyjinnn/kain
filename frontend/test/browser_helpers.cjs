const { spawn } = require('node:child_process');
const path = require('node:path');
const { chromium } = require('playwright');
const fs = require('node:fs');
const os = require('node:os');
async function harness(port, run, options = {}) {
  const base = `http://127.0.0.1:${port}`;
  let occupied=false;try{occupied=(await fetch(base+'/api/state')).ok;}catch{}if(occupied)throw new Error('Test port already in use: '+port);
  const taskDir=fs.mkdtempSync(path.join(os.tmpdir(),'sisa-browser-'));
  const serverArgs = options.imageFixture ? ['-c', `from pathlib import Path; from backend.services import ai; import uvicorn; content=Path(${JSON.stringify(options.imageFixture)}).read_bytes(); ai.available=lambda:True; ai.image_available=lambda:True; ai.generate_image=lambda *args:(content,{'source':'test_fixture','media_type':'image/png'}); uvicorn.run('backend.app:app',host='127.0.0.1',port=${port})`] : ['-m','uvicorn','backend.app:app','--host','127.0.0.1','--port',String(port)];
  const server = spawn('python', serverArgs, {cwd:path.resolve(__dirname,'../..'),env:{...process.env,SISA_DATA_DIR:taskDir,NINEROUTER_KEY:'',OPENAI_API_KEY:'',SISA_IMAGE_MODEL:''},stdio:'ignore'});
  let browser;
  try {
    for(let i=0;i<80;i++){try{if((await fetch(base+'/api/state')).ok)break;}catch{}await new Promise(r=>setTimeout(r,200));}
    browser=await chromium.launch({headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
    const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(String(e)));
    const idle=()=>page.waitForFunction(()=>!document.getElementById('demoStart').disabled);
    const shot=async(name,phase=1)=>{
      const dir=path.resolve(__dirname,`../../artifacts/phase-${phase}/after`);fs.mkdirSync(dir,{recursive:true});
      // Allow ResizeObserver to finish before the compositor captures the canvas.
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      const mobile3D=page.viewportSize().width<700&&await page.locator('#pouchScene canvas').isVisible();
      // Snapshot the actual canvas pixels, then restore the live canvas. This
      // avoids a Windows software-GL compositor stall after an axe audit.
      const frozen=await page.evaluate(async()=>{const canvas=document.querySelector('#pouchScene canvas');if(!canvas)return false;const bitmap=document.createElement('img');bitmap.alt='';bitmap.style.cssText='width:100%;height:100%;display:block';bitmap.src=canvas.toDataURL('image/png');await bitmap.decode();canvas.replaceWith(bitmap);window.__captureCanvas={canvas,bitmap};return true;});
      try{await page.screenshot({path:path.join(dir,name+'.png'),fullPage:true});}
      finally{if(frozen)await page.evaluate(()=>{const {canvas,bitmap}=window.__captureCanvas;bitmap.replaceWith(canvas);delete window.__captureCanvas;});}
    };
    await page.goto(base+'/?test=1');await idle();
    await run({base,browser,context,page,idle,errors,shot});
  }finally{await browser?.close();server.kill();await new Promise(resolve=>{if(server.exitCode!==null)resolve();else server.once('exit',resolve);});if(path.resolve(taskDir).startsWith(path.resolve(os.tmpdir())+path.sep)&&path.basename(taskDir).startsWith('sisa-browser-')){try{fs.rmSync(taskDir,{recursive:true});}catch{}}}
}
module.exports={harness};
