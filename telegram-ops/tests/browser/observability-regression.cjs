process.env.API_DELAY_MS='50';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'/Users/hahaha/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const f=require('./response-fixture.cjs');
const out=path.resolve(__dirname,'../../../artifacts/health-performance');
(async()=>{
 fs.mkdirSync(out,{recursive:true});await new Promise(r=>f.server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'}),report=[];
 for(const width of [1440,390]){
  const p=await browser.newPage({viewport:{width,height:width===390?844:1000}}),errors=[];p.on('pageerror',e=>errors.push(e.message));
  await p.addInitScript(()=>{const original=setInterval;window.setInterval=(fn,ms,...rest)=>ms===15000?0:original(fn,ms,...rest);});
  const nav=async name=>{await p.locator(`nav [data-page=${name}]`).click();await p.waitForFunction(name=>page===name&&!navigationPending,name);};
  await p.goto('http://127.0.0.1:'+f.server.address().port+'/console');await p.locator('[data-health-kind=relay]').waitFor();
  assert.equal(await p.locator('#performance-panel').getAttribute('open'),null);
  assert.match(await p.locator('[data-health-kind=relay]').innerText(),/累计 2 条失败/);
  await p.locator('#task-health-panel [data-diagnose-task="1"]').click();await p.getByText('命中排除词',{exact:true}).waitFor();await p.locator('#close-modal').click();
  f.failNext('/api/overview');await p.locator('#refresh-overview').click();await p.locator('#task-health-panel .callout').waitFor();
  assert.match(await p.locator('[data-health-kind=relay]').innerText(),/测试任务/);
  await p.locator('#refresh-overview').click();await p.locator('#task-health-panel .callout').waitFor({state:'detached'});
  await p.locator('#task-health-panel').screenshot({path:path.join(out,`health-${width}.png`)});
  await nav('ai');await p.getByRole('tab',{name:'分析设置',exact:true}).click();await p.locator('#ai-form .form-expand').click();
  await p.locator('#ai-form [name=message_count]').fill('61');
  await p.getByRole('button',{name:'保存摘要设置',exact:true}).click();await p.waitForFunction(()=>!writeRequests&&!document.querySelector('#ai-form button[type=submit]').disabled);
  await nav('overview');await p.locator('#performance-panel summary').click();
  const timings=p.locator('#browser-timings');assert.match(await timings.innerText(),/保存 AI 设置/);assert.match(await timings.innerText(),/切页 · AI 群组分析/);
  // Query strings and submitted body values never appear in timing data.
  await p.evaluate(async()=>{await api('/records?q=NEVER-EXPOSE&task_id=987654');await api('/guards','POST',{note:'NEVER-EXPOSE',user_id:987654});});
  await p.locator('#refresh-browser-timings').click();assert(!JSON.stringify(await p.evaluate(()=>ConsoleTiming.groups())).includes('NEVER-EXPOSE'));
  assert(!JSON.stringify(await p.evaluate(()=>ConsoleTiming.groups())).includes('987654'));
  f.failNext('/api/records');await p.evaluate(async()=>{try{await api('/records')}catch{}});
  const failed=await p.evaluate(()=>ConsoleTiming.groups().find(r=>r.kind==='api'&&r.title==='读取 · 消息记录'));assert.equal(failed.errors,1);
  await p.evaluate(()=>{window.keptOverviewButton=document.querySelector('#refresh-overview');window.keptTimingButton=document.querySelector('#refresh-browser-timings');window.keptDiag=document.querySelector('#task-health-panel [data-diagnose-task]');});
  const before=f.calls.length;await p.locator('#refresh-browser-timings').click();assert.equal(f.calls.length,before,'local timing refresh must not request server');
  await p.locator('#refresh-overview').click();await p.waitForFunction(()=>!document.querySelector('#refresh-overview').disabled);assert(await p.locator('#performance-panel').getAttribute('open')!==null);
  await p.evaluate(async()=>{const data=await readOverview();await render({background:true,revision:renderRevision,data});});
  assert(await p.evaluate(()=>keptOverviewButton===document.querySelector('#refresh-overview')&&keptTimingButton===document.querySelector('#refresh-browser-timings')&&keptDiag===document.querySelector('#task-health-panel [data-diagnose-task]')));
  await p.locator('#refresh-browser-timings').click();
  assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await p.screenshot({path:path.join(out,`workbench-${width}.png`),fullPage:true});
  assert.deepEqual(errors,[]);report.push({width,healthStates:true,diagnostics:true,failureRetainsSnapshot:true,navigationAndSaveTiming:true,queryAndBodyExcluded:true,localRefreshNoRequest:true,expandedStateRetained:true,errors});await p.close();
 }
 await browser.close();f.server.close();fs.writeFileSync(path.join(out,'regression.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
})().catch(e=>{console.error(e);process.exit(1)});
