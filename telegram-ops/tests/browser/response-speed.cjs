// Repeatable UI timing with fictional data and an identical delayed API before/after.
const fs=require('node:fs');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || '/Users/hahaha/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fixtures=require('./response-fixture.cjs');
const {server,state,calls,delay}=fixtures;
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_BIN||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const p=await browser.newPage({viewport:{width:1440,height:1000}});
 p.on('dialog',d=>d.accept());
  const errors=[];p.on('pageerror',e=>errors.push(e.message));
 const samples=[];
 async function measure(name,fn){const index=calls.length,begin=Date.now();await fn();samples.push({name,ms:Date.now()-begin,requests:calls.slice(index).map(c=>({path:c.path,method:c.method,startMs:c.start-begin,endMs:c.end?c.end-begin:null}))});}
 async function nav(name,title){await p.locator('nav [data-page="'+name+'"]').click();await p.locator('#content h1').filter({hasText:title}).waitFor();}
 await measure('cold_records',async()=>{await p.goto(origin+'/console/records');await p.locator('#content tbody tr').first().waitFor();});
 await measure('records_to_ai',()=>nav('ai','AI 群组分析'));
 await p.getByRole('tab',{name:'分析设置',exact:true}).click();await p.locator('#ai-form .form-expand').click();
 await p.locator('#ai-form [name=message_count]').fill('60');
 await measure('save_ai',async()=>{await p.getByRole('button',{name:'保存摘要设置',exact:true}).click();await p.waitForFunction(()=>document.querySelector('#toast').textContent==='摘要设置已保存'&&document.querySelector('#ai-form [name=message_count]').value==='60');});
 await measure('ai_to_channels',()=>nav('channels','频道采编'));
 await p.getByRole('tab',{name:'采集设置',exact:true}).click();
 await p.locator('[data-feed-edit="1"]').click();await p.locator('#channel-feed-form [name=name]').fill('保存后的采集');
 await measure('save_channel',async()=>{await p.getByRole('button',{name:'保存采集设置',exact:true}).click();await p.locator('.channel-feed strong').filter({hasText:'保存后的采集'}).waitFor();});
 await measure('channels_to_accounts',()=>nav('accounts','账号管理'));
 await p.locator('[data-account-tab="sender"]').click();await p.getByRole('link',{name:'管理私信接收',exact:true}).click();await p.locator('[data-weight="2"]').click();await p.locator('#weight-form [name=weight]').fill('7');
 await measure('save_weight',async()=>{await p.getByRole('button',{name:'保存权重',exact:true}).click();await p.waitForFunction(()=>document.querySelector('#toast').textContent==='轮询权重已保存');});
 await measure('private_to_relay_tasks',async()=>{await nav('tasks','监测转发');await p.waitForFunction(()=>!navigationPending);});
 await p.getByRole('tab',{name:'监测任务',exact:true}).click();
 await p.locator('[data-edit-task="1"]').click();await p.locator('#task-form [name=name]').fill('保存后的任务');
 await measure('save_task',async()=>{await p.getByRole('button',{name:'保存更改并暂停',exact:true}).click();await p.getByText('保存后的任务',{exact:true}).waitFor();});
 assert.deepEqual(errors,[]);assert.equal(state.tasks[0].enabled,false);assert.equal(fixtures.feed.enabled,false);assert.equal(fixtures.ai.message_count,60);assert.equal(state.accounts[1].rotation_weight,7);
 const report={apiDelayMs:delay,samples,errors,scope:'Local browser, fictional API responses; no Telegram or AI calls'};
 if(process.env.REPORT)fs.writeFileSync(process.env.REPORT,JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify(report));await browser.close();server.close();
})().catch(e=>{console.error(e);process.exit(1)});
