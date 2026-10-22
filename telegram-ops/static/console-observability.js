/* Measurements stay in this tab; no bodies, queries, IDs, credentials or telemetry uploads. */
const ConsoleTiming = (() => {
  const samples=[], openedAt=new Date().toISOString();
  const pages={overview:'工作台',accounts:'账号管理',tasks:'监测转发',records:'消息记录',guards:'不再联系',ai:'AI 群组分析',broadcasts:'群发任务',channels:'频道采编',settings:'系统设置'};
  const forms={'task-form':'保存监测规则','ai-form':'保存 AI 设置','channel-feed-form':'保存采集设置','channel-draft-form':'保存采编草稿','receive-groups-form':'保存私信接收','weight-form':'保存账号权重','application-form':'保存连接设置','broadcast-form':'保存群发任务','guard-form':'保存名单','password-form':'保存后台密码'};
  const endpoints={'/state':'运行状态','/overview':'工作台概览','/records':'消息记录','/guards':'不再联系名单','/ai/settings':'AI 设置','/ai/activity':'AI 运行记录','/ai/preview':'AI 预览','/ai/run':'AI 推送','/ai/test':'AI 连接测试','/tasks':'监测任务','/tasks/{id}':'监测任务','/tasks/{id}/toggle':'任务启停','/accounts/{id}/listener':'任务诊断','/accounts/{id}/weight':'账号权重','/accounts/{id}/receive-groups':'私信接收','/accounts/{id}/sync':'群组同步','/accounts/{id}/permissions':'发送权限','/channel-feeds':'采集来源','/channel-feeds/{id}':'采集设置','/channel-drafts':'采编结果','/broadcasts':'群发任务','/broadcasts/{id}/start':'启动群发','/broadcasts/{id}/pause':'暂停群发','/broadcasts/{id}/cancel':'取消群发','/guards/{id}':'移除名单','/records/{id}/cancel':'取消待发送消息','/application':'连接设置','/password':'后台密码'};
  function start(kind,key,method='GET') {
    const path=kind==='api'?key.split('?')[0].replace(/\/\d+(?=\/|$)/g,'/{id}'):key;
    const title=kind==='api'?endpoints[path]:kind==='navigation'?pages[path]:forms[path];
    if(!title)return ()=>{};
    const started=performance.now();
    return (ok=true,serverMs=null)=>{
      const ms=Math.round((performance.now()-started)*10)/10;
      samples.push({kind,key:kind==='api'?method+' '+path:path,title:kind==='api'?(method==='GET'?'读取':'提交')+' · '+title:title,ms,ok,serverMs:Number.isFinite(serverMs)?serverMs:null});
      if(samples.length>200)samples.shift();
    };
  }
  function groups() {
    const grouped=new Map();
    for(const s of samples){const key=s.kind+':'+s.key;if(!grouped.has(key))grouped.set(key,[]);grouped.get(key).push(s);}
    return [...grouped.values()].map(items=>{
      const times=items.map(s=>s.ms).sort((a,b)=>a-b), last=items.at(-1);
      return {kind:last.kind,title:last.title,count:items.length,errors:items.filter(s=>!s.ok).length,last_ms:last.ms,p95_ms:times[Math.ceil(times.length*.95)-1],max_ms:times.at(-1)};
    }).sort((a,b)=>b.p95_ms-a.p95_ms);
  }
  function routeTitle(route){
    const [method,path]=route.split(' '), normalized=(path||'').replace(/^\/api/,'').replace(/\{[^}]+\}/g,'{id}');
    return endpoints[normalized]?(method==='GET'?'读取':'提交')+' · '+endpoints[normalized]:route;
  }
  return {start,groups,openedAt,routeTitle};
})();

let overviewSnapshot=null, overviewFailure='', performanceExpanded=false;
function healthTime(value){return value?new Date(/[Zz]|[+-]\d\d:\d\d$/.test(value)?value:value+'Z').toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'暂无记录';}
function healthPanel() {
  const snapshot=overviewSnapshot;
  const rows=[...(snapshot?.tasks||[])].sort((a,b)=>({error:0,warning:1,active:2,paused:3}[a.status]??4)-({error:0,warning:1,active:2,paused:3}[b.status]??4));
  const names={relay:'监测转发',channel:'频道采编',ai:'AI 分析',broadcast:'群发任务'};
  return `<section class="panel health-panel" id="task-health-panel"><div class="panel-head"><h2>任务运行情况</h2><button class="secondary small" id="refresh-overview">刷新概览</button></div>
    ${overviewFailure?'<div class="callout" role="status">运行概览读取失败，'+(snapshot?'以下为上次成功读取的结果。':'请点“刷新概览”重试。')+'</div>':''}
    ${rows.length?`<div class="health-rows">${rows.map(t=>{
      const attention=t.failed||t.unknown?`累计 ${t.failed||0} 条失败 / ${t.unknown||0} 条待核实`:t.exceptions?`累计 ${t.exceptions} 条失败或待核实`:t.latest_status==='failed'?'最近一次摘要失败':t.latest_status==='unknown'?'最近一次摘要结果待核实':'';
      let plan='';
      if(t.kind==='ai')plan=t.next_at?'计划 '+healthTime(t.next_at):t.trigger==='count'&&t.status==='active'?`满 ${t.trigger_count} 条后触发`:'';
      if(t.kind==='broadcast'&&t.next_at)plan='计划 '+healthTime(t.next_at);
      return `<article class="health-row" data-health-kind="${t.kind}" data-health-id="${t.id}"><div class="health-name"><strong>${E(t.name)}</strong><small>${names[t.kind]}</small></div><div class="health-state">${badge({active:'active',error:'failed',warning:'waiting',paused:'disabled'}[t.status],t.label)}${attention?`<a class="health-warning" href="${E(t.href)}">${E(attention)}</a>`:''}${plan?`<small>${E(plan)}${t.kind==='ai'&&t.next_at?'（北京时间）':''}</small>`:''}</div><div class="health-time"><span>${t.kind==='broadcast'?'计划发送':'最近接收'}</span><strong>${E(healthTime(t.kind==='broadcast'?t.next_at:t.last_received))}</strong></div><div class="health-time"><span>${t.kind==='relay'?'最近转发成功':t.kind==='ai'?'最近成功摘要记录':'最近发布成功'}</span><strong>${E(healthTime(t.last_success))}</strong></div><div class="actions">${t.kind==='relay'?`<button class="secondary small" data-diagnose-task="${t.id}">诊断</button>`:''}<a class="button secondary small" href="${E(t.href)}">${t.action}</a></div></article>`;
    }).join('')}</div>`:snapshot?'<p class="page-note">暂无配置任务，可从下方常用功能开始。</p>':''}
    <p class="page-note">${snapshot?'更新于 '+E(healthTime(snapshot.checked_at))+'（北京时间）。':''} 来源群没有新消息不代表故障。监测接收时间仅记录本次服务运行中最近 1000 个群；发送结果取自保存记录。AI 计划时间不保证准时交付。</p></section>`;
}
function durationLabel(ms){return ms<1000?Math.round(ms)+' ms':(ms/1000).toFixed(2)+' 秒';}
function timingTable(rows, browser=false){
  if(!rows.length)return '<p class="page-note">暂无样本。正常切页、保存后回到这里查看。</p>';
  return `<div class="timing-scroll"><table class="timing-table"><thead><tr><th>${browser?'操作':'服务器接口'}</th><th>次数</th><th>未成功</th><th>95% 耗时不超过</th><th>最慢</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${E(browser?({navigation:'切页',save:'保存',api:'接口'}[r.kind]+' · '+r.title):ConsoleTiming.routeTitle(r.route))}</td><td>${r.count}</td><td>${r.errors}</td><td>${durationLabel(r.p95_ms)}</td><td>${durationLabel(r.max_ms)}</td></tr>`).join('')}</tbody></table></div>`;
}
function performancePanel(){
  const server=overviewSnapshot?.performance;
  return `<details class="panel performance-panel" id="performance-panel" ${performanceExpanded?'open':''}><summary>响应速度 <small>查看切页、保存和接口耗时</small></summary><div class="performance-content"><h3>当前页面会话</h3><p class="page-note">最近 200 次操作；刷新或关闭网页后清空。耗时从开始操作到页面更新或接口返回完成，包含网络等待。失败、取消或读取超时计入“未成功”，不等于发送失败。</p><div id="browser-timings">${timingTable(ConsoleTiming.groups(),true)}</div><button class="secondary small" id="refresh-browser-timings">更新本页统计</button><h3>服务器处理</h3><p class="page-note">最近 30 分钟，最多 1000 次接口请求；服务器重启后清空。${server?'当前 '+server.samples+' 个样本。':''}仅统计服务器处理时间，不含网络传输和网页绘制；少量样本仅供参考。运行概览自身不计入。</p>${overviewFailure?'<p class="page-note">统计读取失败，以下可能为旧数据。</p>':''}${timingTable(server?.rows||[])}<p class="page-note">统计只记录固定页面 / 接口名称、耗时和结果，不记录消息正文、查询内容或密钥。</p></div></details>`;
}
async function readOverview(){
  try{return {snapshot:await api('/overview')};}catch(error){return {error:true};}
}
function applyOverview(data){
  if(data?.snapshot){overviewSnapshot=data.snapshot;overviewFailure='';}
  else if(data?.error)overviewFailure='error';
}
async function refreshOverview(){
  const button=$('#refresh-overview');if(button)button.disabled=true;
  const revision=renderRevision;
  const result=await readOverview();
  if(page!=='overview'||revision!==renderRevision)return;
  applyOverview(result);replaceRegion('#task-health-panel',healthPanel());replaceRegion('#performance-panel',performancePanel());
}
function bindObservability(){
  if($('#refresh-overview'))$('#refresh-overview').onclick=refreshOverview;
  if($('#refresh-browser-timings'))$('#refresh-browser-timings').onclick=()=>{$('#browser-timings').innerHTML=timingTable(ConsoleTiming.groups(),true);};
  const panel=$('#performance-panel');if(panel)panel.ontoggle=()=>{if(panel.isConnected)performanceExpanded=panel.open;};
}
