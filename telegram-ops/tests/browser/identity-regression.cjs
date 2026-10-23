// Fictional API writes only. Verify partial updates, repeated clicks and mobile layout.
process.env.API_DELAY_MS='40';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'/Users/hahaha/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const f=require('./response-fixture.cjs');
const out=path.resolve(__dirname,'../../../artifacts/priority-identity');
(async()=>{
 fs.mkdirSync(out,{recursive:true});
 await new Promise(resolve=>f.server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_BIN||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const report=[];
 try {
  for(const width of [1440,390]) {
   Object.assign(f.state.accounts[0],{name:'测试监测',username:null,identity_synced_at:null});
   const p=await browser.newPage({viewport:{width,height:1000}});
   const errors=[];p.on('pageerror',e=>errors.push(e.message));
   await p.addInitScript(()=>{const interval=window.setInterval;window.setInterval=(fn,ms,...args)=>ms===15000?0:interval(fn,ms,...args)});
   await p.goto('http://127.0.0.1:'+f.server.address().port+'/console/accounts');
   await p.locator('[data-sync-identity="1"]').waitFor();
   const reads=f.calls.filter(c=>c.path==='/api/state').length;
   await p.evaluate(()=>window.originalAccountList=document.querySelector('.account-list'));
   const hold=f.holdNext('/api/accounts/1/identity/sync');
   const before=f.calls.filter(c=>c.path.endsWith('/identity/sync')).length;
   await p.locator('[data-sync-identity="1"]').click();await hold.started;
   assert(await p.locator('[data-sync-identity="1"]').isDisabled());
   assert.equal(f.calls.filter(c=>c.path.endsWith('/identity/sync')).length,before+1);
   hold.release();
   await p.waitForFunction(()=>document.querySelector('.account-username')?.textContent==='@example_username_with_long_name');
   assert.equal(f.calls.filter(c=>c.path==='/api/state').length,reads);
   assert(await p.evaluate(()=>originalAccountList===document.querySelector('.account-list')));
   assert.equal(await p.locator('.account-identity img').count(),0);
   assert((await p.locator('.account-identity h3').innerText()).includes('<img src=x'));
   assert(await p.locator('.identity-sync-time').isVisible());
   assert(!(await p.locator('[data-sync-identity="1"]').isDisabled()));
   f.failNext('/api/accounts/1/identity/sync');
   await p.locator('[data-sync-identity="1"]').click();
   await p.waitForFunction(()=>!document.querySelector('[data-sync-identity="1"]').disabled);
   assert.equal(await p.locator('.account-username').innerText(),'@example_username_with_long_name');
   assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   assert.equal(errors.length,0);
   await p.screenshot({path:path.join(out,`accounts-${width}.png`),fullPage:true});
   report.push({width,partialUpdate:true,noStateReload:true,escaped:true,failurePreservesData:true,noOverflow:true});
   await p.close();
  }
  fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
 } finally {await browser.close();await new Promise(resolve=>f.server.close(resolve));}
})().catch(e=>{console.error(e);process.exit(1)});
