// Run: PLAYWRIGHT_MODULE=/path/to/playwright node tests/browser/phase2.cjs
// Self-contained fictional API; no Telegram or production database access.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const { server, fixture, gate } = require('./helpers.cjs');
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const origin=`http://127.0.0.1:${server.address().port}`;
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const evidence=[];
  try {
    for (const mobile of [false,true]) {
      const context=await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:1000},isMobile:mobile,hasTouch:mobile});
      await context.addInitScript(()=>{
        window.tick=null; const original=setInterval;
        window.setInterval=(fn,ms)=>ms===15000 ? (window.tick=fn,1) : original(fn,ms);
      });
      const p=await context.newPage(), errors=[], calls=[]; let state=fixture(), stateGate, recordsGate, serviceGate, failRecords=false, failState=false;
      p.on('pageerror',e=>errors.push(e.message));
      await p.route('**/api/**',async route=>{
        const url=new URL(route.request().url()); calls.push(url.pathname+url.search);
        const reply=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
        if(url.pathname==='/api/state') { if(stateGate) await stateGate.promise; return failState?reply({detail:'状态读取失败'},500):reply(state); }
        if(url.pathname==='/api/records') {
          if(recordsGate) await recordsGate.promise;
          if(failRecords) return reply({detail:'模拟读取失败，请重试'},500);
          const offset=Number(url.searchParams.get('offset'));return reply({total:120,items:Array.from({length:50},(_,i)=>({id:offset+i+1,task_id:1,stage:'relay',username:'user_'+(offset+i),chat_id:-1001,source_title:'测试群',text:'记录'+(offset+i),original_text:'咨询',status:'sent',created_at:'2026-09-22T00:00:00'}))});
        }
        if(url.pathname.startsWith('/api/service/')) {if(serviceGate)await serviceGate.promise;state.running=!state.running;return reply({});}
        if(url.pathname==='/api/accounts/2/weight'){state.accounts[1].rotation_weight=route.request().postDataJSON().weight;return reply({});}
        if(url.pathname==='/api/accounts/2/receive-groups'){const data=route.request().postDataJSON();state.accounts[1].dm_template=data.template;state.accounts[1].receive_chat_ids=data.chat_ids;return reply({});}
        if(url.pathname==='/api/tasks/1'){Object.assign(state.tasks[0],route.request().postDataJSON(),{enabled:false});return reply({});}
        throw Error('Unexpected API '+url.pathname);
      });
      const goto=async page=>{await p.goto(origin+'/console/'+page);await p.locator('#content h1').waitFor();};
      await goto('overview');
      await p.evaluate(()=>{window.savedNode=document.querySelector('[data-edit-task="1"]');window.mutations=0;new MutationObserver(l=>window.mutations+=l.length).observe(document.querySelector('#content'),{childList:true});});
      await p.evaluate(async()=>{await tick();await tick();});
      assert.equal(await p.evaluate(()=>savedNode===document.querySelector('[data-edit-task="1"]')),true);
      assert.equal(await p.evaluate(()=>mutations),0);
      await p.locator('[data-edit-task="3"]').focus();await p.evaluate(()=>scrollTo(0,400));
      const scroll=await p.evaluate(()=>scrollY);state.stats.hits=2;
      await p.evaluate(()=>tick());assert.equal(await p.locator('.metric b').first().innerText(),'2');
      assert.equal(await p.evaluate(()=>document.activeElement.getAttribute('data-edit-task')),'3');
      assert.equal(await p.evaluate(()=>scrollY),scroll);
      // A polling request already in flight when an editor opens must not replace its opener.
      stateGate=gate();await p.evaluate(()=>{window.pendingTick=tick();});
      await p.locator('[data-edit-task="1"]').click();
      for(const [name,value] of [['keywords','新词 草稿'],['source_chats','-1008'],['relay_chat','-1009']]) await p.locator(`[name=${name}]`).fill(value);
      state.stats.hits=3;stateGate.release();stateGate=null;await p.evaluate(()=>pendingTick);
      assert.equal(await p.locator('[name=keywords]').inputValue(),'新词 草稿');
      const requests=calls.length;await p.evaluate(()=>tick());assert.equal(calls.length,requests);
      await p.locator('#task-form button[type=submit]').click();await p.locator('#modal').waitFor({state:'hidden'});
      assert.equal(state.tasks[0].keywords,'新词 草稿');assert.equal(state.tasks[0].relay_chat,-1009);
      // Service pending state must survive a background status refresh.
      serviceGate=gate();await p.locator('#service-button').click();await p.evaluate(()=>tick());assert.equal(await p.locator('#service-button').isDisabled(),true);
      serviceGate.release();serviceGate=null;await p.waitForFunction(()=>!document.querySelector('#service-button').disabled);
      await goto('accounts');await p.locator('[data-account-tab=sender]').click();
      await p.locator('[data-receive-groups]').click();await p.locator('[name=template]').fill('尚未保存的文案');await p.evaluate(()=>tick());assert.equal(await p.locator('[name=template]').inputValue(),'尚未保存的文案');
      await p.locator('#receive-groups-form button[type=submit]').click();await p.locator('#modal').waitFor({state:'hidden'});
      await p.locator('[data-weight]').click();await p.locator('[name=weight]').fill('7');await p.evaluate(()=>tick());assert.equal(await p.locator('[name=weight]').inputValue(),'7');
      await p.locator('#weight-form button[type=submit]').click();await p.locator('#modal').waitFor({state:'hidden'});assert.equal(state.accounts[1].rotation_weight,7);
      await goto('records');
      const first=()=>p.locator('tbody tr').first().innerText();const old=await first();
      failRecords=true;await p.locator('#next-page').click();await p.waitForFunction(()=>!document.querySelector('#content').hasAttribute('aria-busy'));
      assert.match(await p.locator('#toast').innerText(),/模拟读取失败/);assert.equal(await first(),old);assert.equal(await p.locator('#prev-page').isDisabled(),true);
      failRecords=false;await p.locator('#next-page').click();await p.waitForFunction(()=>!document.querySelector('#content').hasAttribute('aria-busy'));assert.match(await first(),/user_50/);
      assert.match(calls.filter(x=>x.startsWith('/api/records')).at(-1),/offset=50$/);
      await p.locator('#prev-page').click();await p.waitForFunction(()=>!document.querySelector('#content').hasAttribute('aria-busy'));assert.match(await first(),/user_0/);
      await p.locator('#next-page').click();await p.waitForFunction(()=>!document.querySelector('#content').hasAttribute('aria-busy'));assert.match(await first(),/user_50/);
      await p.locator('#record-search').fill('保留输入');await p.locator('#record-status').selectOption('sent');
      failRecords=true;await p.locator('#search-records').click();await p.waitForFunction(()=>!document.querySelector('#content').hasAttribute('aria-busy'));
      assert.equal(await p.locator('#record-search').inputValue(),'保留输入');assert.equal(await p.locator('#record-status').inputValue(),'sent');assert.match(await first(),/user_50/);
      failRecords=false;recordsGate=gate();const count=calls.filter(x=>x.startsWith('/api/records')).length;
      await p.locator('#search-records').click();await p.waitForFunction(()=>document.querySelector('#content').getAttribute('aria-busy')==='true');
      assert.equal(await p.locator('#next-page').isDisabled(),true);assert.equal(await p.locator('#record-search').isDisabled(),true);
      await p.evaluate(()=>document.querySelector('#search-records').onclick({currentTarget:document.querySelector('#search-records')}));
      assert.equal(calls.filter(x=>x.startsWith('/api/records')).length-count,1);
      recordsGate.release();recordsGate=null;await p.waitForFunction(()=>!document.querySelector('#content').hasAttribute('aria-busy'));
      assert.match(await first(),/user_0/);assert.equal(await p.locator('#record-search').inputValue(),'保留输入');
      failRecords=true;await p.locator('#refresh-records').click();await p.waitForFunction(()=>!document.querySelector('#content').hasAttribute('aria-busy'));assert.match(await p.locator('#toast').innerText(),/模拟读取失败/);failRecords=false;
      await p.locator('#refresh-records').click();await p.waitForFunction(()=>!document.querySelector('#content').hasAttribute('aria-busy'));
      assert.equal(await p.locator('#toast').innerText(),'记录已更新');
      assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);
      evidence.push({mobile,unchangedOverviewTicks:2,unchangedContentReplacements:0,errors,scenarios:'changed metrics/focus/scroll, in-flight modal draft, pending service, copy/weight save, pagination rollback, filter retry, double click, refresh failure/recovery'});
      if(process.env.SCREENSHOT_DIR) await p.screenshot({path:path.join(process.env.SCREENSHOT_DIR,mobile?'records-mobile.png':'records-desktop.png'),fullPage:false});
      await context.close();
    }
    console.log(JSON.stringify(evidence,null,2));
  } finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
