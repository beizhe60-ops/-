// Run with the same PLAYWRIGHT_MODULE environment as phase2.cjs.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { server, fixture, gate } = require('./helpers.cjs');
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const origin=`http://127.0.0.1:${server.address().port}`;
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const results=[];
  try {
    for(const mobile of [false,true]) {
      const c=await browser.newContext({viewport:mobile?{width:390,height:844}:{width:1440,height:1000},isMobile:mobile,hasTouch:mobile});
      await c.addInitScript(()=>{
        const interval=setInterval, timeout=setTimeout, clear=clearTimeout;
        window.setInterval=(fn,ms)=>ms===15000?(window.tick=fn,1):interval(fn,ms);
        window.readTimers=new Set();window.fastTimeout=false;
        window.setTimeout=(fn,ms,...args)=>{
          if(ms!==30000)return timeout(fn,ms,...args);
          const id=timeout(()=>{readTimers.delete(id);fn(...args)},fastTimeout?200:ms);readTimers.add(id);return id;
        };
        window.clearTimeout=(id)=>{readTimers.delete(id);clear(id)};
      });
      const p=await c.newPage(), errors=[], calls=[];p.on('pageerror',e=>errors.push(e.message));
      let state=fixture(), holdState=null, holdRecords=null, failState=false, rejectHeldRecords=false, holdService=null;
      await p.route('**/api/**',async route=>{
        const url=new URL(route.request().url());calls.push(url.pathname+url.search);
        const reply=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
        if(url.pathname==='/api/state') {
          const snapshot=structuredClone(state);const wait=holdState;holdState=null;
          if(wait)await wait.promise;
          return failState?reply({detail:'模拟状态故障'},500):reply(snapshot);
        }
        if(url.pathname.startsWith('/api/service/')) {const wait=holdService;holdService=null;if(wait)await wait.promise;state.running=!state.running;state.stats.hits=99;return reply({});}
        if(url.pathname==='/api/records') {
          const wait=holdRecords;holdRecords=null;const reject=rejectHeldRecords;
          if(wait)await wait.promise;
          if(wait && reject)return reply({detail:'过期请求错误'},500);
          return reply({total:120,items:[{id:1,task_id:1,stage:'relay',chat_id:-1001,username:(url.searchParams.get('q')||'base')+'_'+url.searchParams.get('offset'),text:'虚构记录',original_text:'咨询',status:'sent',created_at:'2026-09-23T00:00:00'}]});
        }
        throw Error('Unexpected API '+url.pathname);
      });
      const count=(path)=>calls.filter(x=>x.startsWith(path)).length;
      const goto=async(name)=>{await p.goto(origin+'/console/'+name);await p.locator('#content h1').waitFor();};
      await goto('overview');
      // Two scheduled callbacks while one slow state request is in flight.
      const slow=gate();holdState=slow;let before=count('/api/state');
      const firstSeen=p.waitForRequest(r=>new URL(r.url()).pathname==='/api/state');
      await p.evaluate(()=>{window.firstTick=tick()});await firstSeen;
      await p.evaluate(()=>tick());assert.equal(count('/api/state')-before,1);
      slow.release();await p.evaluate(()=>firstTick);
      // A response captured before a mutation is not reused as the post-save state.
      const stale=gate();holdState=stale;before=count('/api/state');
      const seen=p.waitForRequest(r=>r.url().endsWith('/api/state'));
      await p.evaluate(()=>{window.oldTick=tick()});await seen;
      const saved=p.waitForResponse(r=>r.url().endsWith('/api/service/start'));
      await p.locator('#service-button').click();await saved;
      assert.equal(await p.locator('#service-button').isDisabled(),true);
      stale.release();await p.evaluate(()=>oldTick);
      await p.waitForFunction(()=>!document.querySelector('#service-button').disabled);
      assert.match(await p.locator('#service-badge').innerText(),/运行中/);assert.equal(await p.locator('.metric b').first().innerText(),'99');
      assert.equal(count('/api/state')-before,2);
      // A failed poll is visible, retry works, and all read timers are cleaned up.
      failState=true;await p.evaluate(()=>tick());assert.match(await p.locator('#toast').innerText(),/模拟状态故障/);
      failState=false;await p.evaluate(()=>tick());assert.equal(await p.evaluate(()=>readTimers.size),0);
      await goto('records');before=count('/api/state');const recordBefore=count('/api/records');
      await p.locator('#refresh-records').click();await p.waitForFunction(()=>!document.querySelector('#content').hasAttribute('aria-busy'));
      assert.equal(count('/api/state'),before);assert.equal(count('/api/records')-recordBefore,1);
      // A newer full render wins over an older delayed filter response.
      const late=gate();holdRecords=late;await p.locator('#record-search').fill('old_search');
      const recordSeen=p.waitForRequest(r=>r.url().includes('q=old_search'));
      await p.locator('#search-records').click();await recordSeen;
      await p.locator('#service-button').click();await p.waitForFunction(()=>!document.querySelector('#service-button').disabled);
      late.release();await p.waitForFunction(()=>!document.querySelector('#content').hasAttribute('aria-busy'));
      assert.match(await p.locator('tbody').innerText(),/base_0/);assert.equal(await p.locator('#record-search').inputValue(),'');
      // A stale failed full-page read must not overwrite a newer successful search.
      const oldFailure=gate();holdRecords=oldFailure;rejectHeldRecords=true;
      const oldRead=p.waitForRequest(r=>r.url().includes('/api/records'));
      await p.locator('#service-button').click();await oldRead;
      await p.locator('#record-search').fill('new_search');await p.locator('#search-records').click();
      await p.waitForFunction(()=>!document.querySelector('#content').hasAttribute('aria-busy'));
      oldFailure.release();await p.waitForFunction(()=>!document.querySelector('#service-button').disabled);rejectHeldRecords=false;
      assert.match(await p.locator('tbody').innerText(),/new_search_0/);
      assert.equal(await p.locator('#toast').innerText(),'记录已更新');
      await p.locator('#record-search').fill('');await p.locator('#search-records').click();await p.waitForFunction(()=>!document.querySelector('#content').hasAttribute('aria-busy'));
      // Accelerate only the 30-second read deadline to keep this test bounded.
      await p.evaluate(()=>{fastTimeout=true});const stalled=gate();holdRecords=stalled;
      await p.locator('#next-page').click();await p.waitForFunction(()=>!document.querySelector('#content').hasAttribute('aria-busy'));
      assert.match(await p.locator('#toast').innerText(),/读取超时/);assert.match(await p.locator('tbody').innerText(),/base_0/);
      assert.equal(await p.locator('#next-page').isDisabled(),false);assert.equal(await p.evaluate(()=>readTimers.size),0);
      stalled.release();await p.evaluate(()=>{fastTimeout=false});
      await p.locator('#next-page').click();await p.waitForFunction(()=>!document.querySelector('#content').hasAttribute('aria-busy'));
      assert.match(await p.locator('tbody').innerText(),/base_50/);
      // A timed-out state read releases the shared slot so polling can recover.
      await p.evaluate(()=>{fastTimeout=true});const stalledState=gate();holdState=stalledState;
      await p.evaluate(()=>tick());assert.match(await p.locator('#toast').innerText(),/读取超时/);
      assert.equal(await p.evaluate(()=>readTimers.size),0);stalledState.release();
      await p.evaluate(()=>{fastTimeout=false});await p.evaluate(()=>tick());
      // Mutations are not aborted/retried by the read-only deadline.
      await p.evaluate(()=>{fastTimeout=true});const slowWrite=gate();holdService=slowWrite;
      await p.locator('#service-button').click();await p.waitForTimeout(300);
      assert.equal(await p.locator('#service-button').isDisabled(),true);
      slowWrite.release();await p.waitForFunction(()=>!document.querySelector('#service-button').disabled);
      await p.evaluate(()=>{fastTimeout=false});
      // Hidden/modal ticks do not start more requests.
      await p.evaluate(()=>Object.defineProperty(document,'hidden',{configurable:true,value:true}));before=count('/api/state');await p.evaluate(()=>tick());assert.equal(count('/api/state'),before);
      await p.evaluate(()=>Object.defineProperty(document,'hidden',{configurable:true,value:false}));await p.evaluate(()=>tick());assert.equal(count('/api/state'),before+1);
      assert.deepEqual(errors,[]);assert.equal(await p.evaluate(()=>readTimers.size),0);
      results.push({mobile,slowPollRequests:1,postMutationFreshRead:true,recordRefreshRequests:1,staleRenderDiscarded:true,readTimeoutRecovered:true,remainingReadTimers:0,pageErrors:errors});
      await c.close();
    }
    console.log(JSON.stringify(results,null,2));
  }finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1});
