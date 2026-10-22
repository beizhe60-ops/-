// Regression checks for diagnostics, filters, dirty forms and acknowledged partial updates.
process.env.API_DELAY_MS='35';
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'/Users/hahaha/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const f=require('./response-fixture.cjs');
const out=path.resolve(__dirname,'../../../artifacts/usability-polish');
(async()=>{
 fs.mkdirSync(out,{recursive:true});await new Promise(resolve=>f.server.listen(0,'127.0.0.1',resolve));
 const origin='http://127.0.0.1:'+f.server.address().port;
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_BIN||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const report=[];
 for(const width of [1440,390]){
  f.configure({records:{total:2,tasks:[{id:1,name:'测试任务'},{id:99,name:'已删除任务',deleted:true}],items:[1,2].map(id=>({id,task_id:1,stage:'relay',account_id:1,username:'example_user',user_id:123,source_title:'测试群',text:'测试正文',original_text:'咨询',status:id===1?'pending':'sent',created_at:'2026-10-23T00:00:00'}))},campaigns:[1,2].map(id=>({id,name:'群发 '+id,status:'queued',account_id:1,targets:[-1001],text:'测试通知',counts:{pending:1}})),guards:[{id:1,user_id:123,note:'测试名单'}]});
  f.state.stats.exceptions={failed:3,unknown:1};f.state.stats.failed=4;
  const p=await browser.newPage({viewport:{width,height:width===390?844:1000}});
  await p.addInitScript(()=>{const old=setInterval;window.setInterval=(fn,ms,...args)=>ms===15000?0:old(fn,ms,...args)});
  const errors=[],dialogs=[];let accept=false;p.on('pageerror',e=>errors.push(e.message));p.on('dialog',async d=>{dialogs.push(d.message());await (accept?d.accept():d.dismiss());});
  const nav=async name=>{await p.locator(`nav [data-page="${name}"]`).click();await p.waitForFunction(name=>page===name&&!navigationPending,name);};
  const snap=async name=>{assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,name);await p.screenshot({path:path.join(out,`${name}-${width}.png`)});};
  await p.goto(origin+'/console');await p.locator('.attention-list').waitFor();
  await p.getByRole('link',{name:'累计 1 条结果待核实，查看记录',exact:true}).click();await p.locator('#record-status').waitFor();
  assert.equal(await p.locator('#record-status').inputValue(),'unknown');assert(f.calls.at(-1).query.includes('status=unknown'));
  await p.locator('#record-period').selectOption('7d');await p.locator('#record-task').selectOption('99');await p.locator('#search-records').click();await p.waitForFunction(()=>!recordsPending);
  assert(p.url().includes('task_id=99')&&p.url().includes('period=7d'));const appliedUrl=p.url();
  f.failNext('/api/records');await p.locator('#record-period').selectOption('today');await p.locator('#search-records').click();await p.waitForFunction(()=>!recordsPending);
  assert.equal(p.url(),appliedUrl);assert.equal(await p.evaluate(()=>recordPeriod),'7d');
  await nav('tasks');await p.locator('[data-diagnose-task="1"]').click();await p.getByText('命中排除词',{exact:true}).waitFor();
  assert.equal(await p.getByText('unrelated-event',{exact:true}).count(),0);await snap('diagnostics');
  await p.getByRole('link',{name:'查看此任务记录',exact:true}).click();await p.waitForFunction(()=>page==='records'&&!navigationPending);assert.equal(await p.locator('#record-task').inputValue(),'1');assert.equal(await p.locator('#record-status').inputValue(),'');
  await p.evaluate(()=>{window.keptSearch=document.querySelector('#record-search');window.keptHeading=document.querySelector('.page-heading')});
  await p.locator('#record-search').fill('尚未应用的搜索');const readsBefore=f.calls.length;
  await p.locator('[data-cancel-job="1"]').click();await p.waitForFunction(()=>!document.querySelector('[data-cancel-job="1"]'));
  assert.equal(f.calls.length-readsBefore,1);assert.equal(await p.locator('#record-search').inputValue(),'尚未应用的搜索');assert(await p.evaluate(()=>keptSearch===document.querySelector('#record-search')&&keptHeading===document.querySelector('.page-heading')));await snap('records');
  await nav('tasks');await p.locator('[data-edit-task="1"]').click();
  let n=dialogs.length;await p.locator('[data-picker=source_chats] [data-group-search]').fill('测试群组');await p.locator('#close-modal').click();assert.equal(dialogs.length,n);
  await p.locator('[data-edit-task="1"]').click();await p.locator('#task-form [name=name]').fill('尚未保存');await p.locator('#close-modal').click();assert.equal(dialogs.length,n+1);assert(await p.locator('#task-form').isVisible());
  await p.locator('#task-form [name=name]').press('Escape');assert.equal(dialogs.length,n+2);assert(await p.locator('#task-form').isVisible());accept=true;await p.locator('#close-modal').click();accept=false;
  await p.getByRole('tab',{name:'私信接收',exact:true}).click();await p.locator('[data-receive-groups="2"]').click();await p.locator('#copy-editor').fill('新的图文文案');n=dialogs.length;await p.locator('#close-modal').click();assert.equal(dialogs.length,n+1);assert(await p.locator('#copy-editor').isVisible());accept=true;await p.locator('#close-modal').click();accept=false;
  await nav('ai');await p.getByRole('tab',{name:'分析设置',exact:true}).click();await snap('ai-form');
  assert(!await p.locator('#ai-form [name=message_count]').isVisible());await p.locator('#ai-form .form-expand').click();await p.locator('#ai-form [name=message_count]').fill('65');
  n=dialogs.length;await p.locator('nav [data-page=accounts]').click();assert.equal(dialogs.length,n+1);assert(await p.locator('#ai-form').isVisible());assert.equal(await p.locator('#ai-form [name=message_count]').inputValue(),'65');
  // A rejected Back retains the exact query and editor, not only the page name.
  const aiUrl=p.url();await p.goBack();await p.waitForFunction(()=>!navigationPending);assert.equal(p.url(),aiUrl);assert(await p.locator('#ai-form').isVisible());
  // During save newer edits must remain protected, including cleared secret fields.
  await p.locator('.connection-settings summary').click();await p.locator('#ai-form [name=api_key]').fill('fictional-only');
  const saving=f.holdNext('/api/ai/settings');await p.getByRole('button',{name:'保存摘要设置',exact:true}).click();await saving.started;await p.locator('#ai-form [name=message_count]').fill('66');saving.release();await p.waitForFunction(()=>document.querySelector('#toast').textContent==='摘要设置已保存');
  n=dialogs.length;await p.locator('nav [data-page=accounts]').click();assert.equal(dialogs.length,n+1);assert.equal(await p.locator('#ai-form [name=api_key]').inputValue(),'');
  await p.getByRole('button',{name:'保存摘要设置',exact:true}).click();await p.waitForFunction(()=>!writeRequests&&!document.querySelector('#ai-form button[type=submit]').disabled);
  n=dialogs.length;await nav('accounts');assert.equal(dialogs.length,n);
  await nav('broadcasts');await p.evaluate(()=>{window.otherCampaign=document.querySelector('[data-campaign-row="2"]');window.campaignHeading=document.querySelector('.page-heading')});
  const before=f.calls.length;await p.locator('[data-campaign-pause="1"]').click();await p.locator('[data-campaign-start="1"]').waitFor();assert.equal(f.calls.length-before,1);assert(await p.evaluate(()=>otherCampaign===document.querySelector('[data-campaign-row="2"]')&&campaignHeading===document.querySelector('.page-heading')));
  f.failNext('/api/broadcasts/1/cancel');accept=true;await p.locator('[data-campaign-cancel="1"]').click();await p.waitForFunction(()=>!document.querySelector('[data-campaign-cancel="1"]').disabled);assert(await p.locator('[data-campaign-start="1"]').isVisible());await p.locator('[data-campaign-cancel="1"]').click();await p.waitForFunction(()=>!document.querySelector('[data-campaign-cancel="1"]'));accept=false;
  await nav('guards');await p.evaluate(()=>window.guardHeading=document.querySelector('.page-heading'));accept=true;await p.locator('[data-remove-guard="1"]').click();await p.getByRole('heading',{name:'名单为空',exact:true}).waitFor();accept=false;assert(await p.evaluate(()=>guardHeading===document.querySelector('.page-heading')));
  await p.locator('#new-guard').click();await p.locator('#guard-form [name=user_id]').fill('123');await p.getByRole('button',{name:'加入名单',exact:true}).click();await p.locator('[data-remove-guard]').waitFor();assert(await p.evaluate(()=>guardHeading===document.querySelector('.page-heading')));
  assert.deepEqual(errors,[]);report.push({width,taskDiagnostics:true,recordDeepLinks:true,filterFailureKeepsAppliedQuery:true,modalAndNavigationGuards:true,saveRaceGuard:true,partialActions:true,errors});await p.close();
 }
 await browser.close();f.server.close();fs.writeFileSync(path.join(out,'regression.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
})().catch(e=>{console.error(e);process.exit(1)});
