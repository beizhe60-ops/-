// Behavior checks for incremental navigation/saves. No external messaging clients.
process.env.API_DELAY_MS='40';
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '/Users/hahaha/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const f=require('./response-fixture.cjs');
(async()=>{
 await new Promise(resolve=>f.server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+f.server.address().port;
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_BIN||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const reports=[];
 for(const mobile of [false,true]){
  const p=await browser.newPage({viewport:mobile?{width:390,height:844}:{width:1440,height:1000}});
  await p.addInitScript(()=>{const interval=window.setInterval;window.setInterval=(fn,ms,...args)=>ms===15000?0:interval(fn,ms,...args)});
  const errors=[];p.on('pageerror',e=>errors.push(e.message));
  let documents=0;p.on('request',r=>{if(r.isNavigationRequest())documents++;});
  const count=route=>f.calls.filter(c=>c.path===route).length;
  const nav=async(name,title)=>{await p.locator('nav [data-page="'+name+'"]').click();await p.locator('#content h1').filter({hasText:title}).waitFor();await p.waitForFunction(()=>!document.querySelector('#content').inert);};
  const first=f.calls.length;
  await p.goto(base+'/console/records');await p.locator('tbody tr').first().waitFor();
  const cold=f.calls.slice(first);assert(cold.find(c=>c.path==='/api/records').start<cold.find(c=>c.path==='/api/state').end);
  await p.locator('#record-search').fill('example');await p.locator('#search-records').click();await p.waitForFunction(()=>!document.querySelector('#content').hasAttribute('aria-busy'));
  await nav('accounts','账号管理');await p.locator('[data-account-tab="sender"]').click();
  await p.locator('[data-weight="2"]').click();await p.locator('#weight-form [name=weight]').fill('8');
  // A stale, slow background snapshot must neither block nor undo a confirmed save.
  const stale=f.holdNext('/api/state');
  await p.evaluate(()=>{window.pendingPoll=readState()});await stale.started;
  const beforeState=count('/api/state');
  await p.evaluate(()=>{window.savedSidebar=document.querySelector('.sidebar');window.untouchedCard=document.querySelector('.page-heading')});
  await p.getByRole('button',{name:'保存权重',exact:true}).click();await p.waitForFunction(()=>!document.querySelector('#modal').open);
  assert.equal(count('/api/state'),beforeState);assert.equal(f.state.accounts[1].rotation_weight,8);
  stale.release();await p.evaluate(()=>pendingPoll);
  assert.equal(await p.evaluate(()=>state.accounts.find(a=>a.id===2).rotation_weight),8);
  assert(await p.evaluate(()=>savedSidebar===document.querySelector('.sidebar')&&untouchedCard===document.querySelector('.page-heading')));
  // No document reload for navigation; back/forward retains applied record filters.
  await nav('records','消息记录');assert.equal(await p.locator('#record-search').inputValue(),'example');
  await p.goBack();await p.locator('[data-account-tab="sender"][aria-selected="true"]').waitFor();
  await p.goForward();await p.locator('#record-search').waitFor();assert.equal(await p.locator('#record-search').inputValue(),'example');assert.equal(documents,1);
  // Repeated identical reads are shared while in flight.
  let before=count('/api/ai/activity');await p.evaluate(()=>Promise.all([api('/ai/activity'),api('/ai/activity')]));assert.equal(count('/api/ai/activity')-before,1);
  // Rapid navigation: slower AI reads cannot replace a newer account page.
  const slow=f.holdNext('/api/ai/activity');
  await p.locator('nav [data-page="ai"]').click();await slow.started;assert(await p.locator('#service-button').isDisabled());await nav('accounts','账号管理');
  const slowResponse=p.waitForResponse(r=>r.url().endsWith('/api/ai/activity'));slow.release();await slowResponse;assert.equal(await p.locator('#content h1').innerText(),'账号管理');
  // Navigation errors retain the previous working screen and its URL.
  f.failNext('/api/channel-feeds');await p.locator('nav [data-page="channels"]').click();await p.waitForFunction(()=>!document.querySelector('#content').inert);
  assert.equal(await p.locator('#content h1').innerText(),'账号管理');assert(p.url().endsWith('/console/accounts'));
  // A rejected write preserves both entered values and the previous task state.
  await nav('tasks','监测转发');await p.locator('[data-edit-task="1"]').click();
  const original=f.state.tasks[0].name;await p.locator('#task-form [name=name]').fill('失败后保留输入');f.failNext('/api/tasks/1');
  await p.getByRole('button',{name:'保存更改并暂停',exact:true}).click();await p.locator('#task-form .form-error').filter({hasText:'模拟请求失败'}).waitFor();
  assert.equal(await p.locator('#task-form [name=name]').inputValue(),'失败后保留输入');assert.equal(f.state.tasks[0].name,original);
  await p.evaluate(()=>{window.otherTask=document.querySelector('[data-task-row="2"]')});
  await p.getByRole('button',{name:'保存更改并暂停',exact:true}).click();await p.waitForFunction(()=>!document.querySelector('#modal').open);
  assert.equal(f.state.tasks[0].enabled,false);assert(await p.evaluate(()=>otherTask===document.querySelector('[data-task-row="2"]')));
  // Toggle uses the server's acknowledged task and keeps the rest of the list.
  const beforeToggle=count('/api/state');await p.locator('[data-toggle-task="1"]').click();
  await p.locator('[data-toggle-task="1"]').filter({hasText:'暂停任务'}).waitFor();
  assert.equal(f.state.tasks[0].enabled,true);assert.equal(count('/api/state'),beforeToggle);
  assert(await p.evaluate(()=>otherTask===document.querySelector('[data-task-row="2"]')));
  // AI save keeps the form, selection and later edits; no second settings read.
  await nav('ai','AI 群组分析');const settingsReads=count('/api/ai/settings');
  await p.evaluate(()=>{window.originalForm=document.querySelector('#ai-form')});
  await p.locator('#ai-form [name=message_count]').fill('70');
  const aiWrite=f.holdNext('/api/ai/settings');const saved=p.waitForRequest(r=>r.method()==='PUT'&&r.url().endsWith('/api/ai/settings'));
  await p.getByRole('button',{name:'保存摘要设置',exact:true}).click();await saved;
  await p.locator('#ai-form [name=message_count]').fill('71');aiWrite.release();
  await p.waitForFunction(()=>document.querySelector('#toast').textContent.includes('摘要设置已保存'));
  assert.equal(f.ai.message_count,70);assert.equal(await p.locator('#ai-form [name=message_count]').inputValue(),'71');
  assert(await p.evaluate(()=>originalForm===document.querySelector('#ai-form')));assert.equal(count('/api/ai/settings')-settingsReads,1);
  // Group search retains selected group IDs while filtering the options.
  await p.locator('[data-picker="source_chat_ids"] [data-group-search]').fill('测试群组 29');
  await p.waitForFunction(()=>document.querySelectorAll('[data-picker="source_chat_ids"] .group-option').length<20);
  assert.equal(await p.locator('[data-picker="source_chat_ids"] [name="source_chat_ids"]').inputValue(),'-1001');
  // Channel save uses the persisted paused result, not an optimistic enabled state.
  await nav('channels','频道采编');await p.locator('[data-feed-edit="1"]').click();await p.locator('#channel-feed-form [name=name]').fill('局部更新的采集');
  await p.evaluate(()=>{window.channelHeading=document.querySelector('.page-heading')});
  const postRead=p.waitForResponse(r=>r.url().includes('/api/channel-drafts'));const feedsBefore=count('/api/channel-feeds');await p.getByRole('button',{name:'保存采集设置',exact:true}).click();await p.locator('.channel-feed strong').filter({hasText:'局部更新的采集'}).waitFor();
  assert.equal(f.feed.enabled,false);assert.equal(count('/api/channel-feeds'),feedsBefore);assert(await p.evaluate(()=>channelHeading===document.querySelector('.page-heading')));
  await postRead;
  // Filter failure rolls back the selection; it does not quietly change query state.
  f.failNext('/api/channel-drafts');await p.locator('#channel-status').selectOption('sent');await p.waitForFunction(()=>!document.querySelector('#channel-status').disabled);
  assert.equal(await p.locator('#channel-status').inputValue(),'all');
  assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  fs.mkdirSync(path.resolve(rootPath(),'artifacts/response-speed'),{recursive:true});
  await p.screenshot({path:path.resolve(rootPath(),`artifacts/response-speed/${mobile?'mobile':'desktop'}.png`),fullPage:true});
  assert.deepEqual(errors,[]);reports.push({mobile,documents,parallelInitialRead:true,staleReadIgnored:true,partialSaves:true,failedWritesKeepInput:true,rapidNavigation:true,filterRollback:true,errors});await p.close();
 }
 console.log(JSON.stringify(reports));fs.writeFileSync(path.resolve(rootPath(),'artifacts/response-speed/regression.json'),JSON.stringify(reports,null,2)+'\n');
 await browser.close();f.server.close();
})().catch(e=>{console.error(e);process.exit(1)});
function rootPath(){return path.resolve(__dirname,'../../..');}
