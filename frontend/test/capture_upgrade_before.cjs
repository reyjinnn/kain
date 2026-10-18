const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
(async () => {
 const root=path.resolve(__dirname,'../..'), dir=path.join(root,'artifacts','phase-1','before'); fs.mkdirSync(dir,{recursive:true});
 const server=spawn('python',['-m','uvicorn','backend.app:app','--host','127.0.0.1','--port','8770'],{cwd:root,env:{...process.env,NINEROUTER_KEY:'',SISA_IMAGE_MODEL:''},stdio:'ignore'});
 let browser;
 try {
  for(let i=0;i<60;i++){try{if((await fetch('http://127.0.0.1:8770/api/state')).ok)break;}catch{} await new Promise(r=>setTimeout(r,200));}
  browser=await chromium.launch({headless:true});
  for(const width of [1440,390]){
   const page=await browser.newPage({viewport:{width,height:1000},reducedMotion:'reduce'}); await page.goto('http://127.0.0.1:8770'); await page.waitForFunction(()=>!document.getElementById('demoStart').disabled);
   await page.screenshot({path:path.join(dir,'cerita-'+width+'.png'),fullPage:true});
   await page.click('#demoStart'); await page.waitForFunction(()=>document.body.dataset.phase==='current_checked'); await page.screenshot({path:path.join(dir,'rancangan-'+width+'.png'),fullPage:true});
   await page.click('#tab-pola'); await page.screenshot({path:path.join(dir,'petunjuk-'+width+'.png'),fullPage:true}); await page.close();
  }
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
