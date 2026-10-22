/* Shared loading/navigation only. Business writes still use the existing endpoints. */
const STATE_MAX_AGE_MS = 15000;
let stateReadAt = 0;
let navigationPending = false;
let writeRequests = 0;
const pageScroll = new Map();
let displayedConsoleUrl = location.pathname + location.search;

function recordPath(query=currentRecordQuery()) {
  return `/records?${recordQueryUrl({...currentRecordQuery(),...query})}`;
}
function channelPath({status=channelFilter,feed=channelFeedFilter,offset=channelOffset}={}) {
  return `/channel-drafts?status=${status}&offset=${offset}${feed?'&feed_id='+Number(feed):''}`;
}
function readPageData(target, url) {
  switch(target) {
    case 'overview': return readOverview();
    case 'records': return api(recordPath(url?recordQueryFromUrl(url):currentRecordQuery()));
    case 'guards': return api('/guards');
    case 'broadcasts': return api('/broadcasts');
    case 'channels': return Promise.all([api('/channel-feeds'),api(channelPath())]);
    case 'ai': return Promise.all([api('/ai/settings'),api('/ai/activity')]);
    default: return Promise.resolve(undefined);
  }
}
function updateChrome() {
  document.title = titles[page] + ' · Telegram Ops';
  $('#crumb').textContent = titles[page];
  $$('[data-page]').forEach(a=>a.classList.toggle('active',a.dataset.page===page));
}

// Only apply fields acknowledged by a successful write. Older reads cannot undo them.
function updateState(change) {
  invalidateStateRead();
  change(state);
  // Other totals can have changed (e.g. cancelled queued messages). Refresh on
  // the next navigation/poll; do not hold up the acknowledged save for totals.
  stateReadAt = 0;
}
function replaceRegion(selector, html) {
  const previous=$(selector);
  if(!previous)return false;
  const fragment=document.createElement('template');fragment.innerHTML=html;
  const next=fragment.content.querySelector(selector);
  if(!next)return false;
  const active=previous.contains(document.activeElement)?document.activeElement:null;
  const attrs=['data-toggle-task','data-edit-task','data-weight','data-permission','data-feed-edit'];
  const attr=attrs.find(a=>active?.hasAttribute(a));
  const focus=attr?`[${attr}="${CSS.escape(active.getAttribute(attr))}"]`:null;
  previous.replaceWith(next);
  bindPage();
  if(focus)next.querySelector(focus)?.focus({preventScroll:true});
  return true;
}
function updateAccountView(id, changes) {
  updateState(s=>Object.assign(s.accounts.find(a=>a.id===id),changes));
  if(page==='tasks')replaceRegion(`[data-sender-task="${id}"]`,senderTasks());
  if(page==='accounts')replaceRegion(`[data-account-card="${id}"]`,accounts());
}
function updateTaskView(task) {
  let oldAccount;
  updateState(s=>{
    const index=s.tasks.findIndex(t=>t.id===task.id);
    oldAccount=s.tasks[index]?.account_a;
    if(index<0)s.tasks.unshift(task);else s.tasks[index]=task;
  });
  if(page==='tasks') {
    if(!replaceRegion(`[data-task-row="${task.id}"]`,tasks()))replaceRegion('#tasks-panel',tasks());
  } else if(page==='accounts') {
    for(const id of new Set([oldAccount,task.account_a].filter(Boolean)))replaceRegion(`[data-account-card="${id}"]`,accounts());
  }
}

async function navigateConsole(url, {pop=false}={}) {
  const target=url.pathname.split('/')[2]||'overview';
  if(!Object.hasOwn(titles,target))return;
  if(writeRequests || servicePending) {
    toast('操作正在处理，请稍候再切换页面');
    if(pop)history.pushState(null,'',displayedConsoleUrl);
    return;
  }
  if(!confirmLeave()){if(pop)history.pushState(null,'',displayedConsoleUrl);return;}
  if($('#modal').open)$('#modal').close();
  const finishTiming=ConsoleTiming.start('navigation',target);
  let navigationSucceeded=false;
  const previousPage=page;
  pageScroll.set(page,[scrollX,scrollY]);
  const revision=++renderRevision;
  navigationPending=true;
  if(state)serviceHeader();
  const content=$('#content');content.inert=true;content.setAttribute('aria-busy','true');
  $('#crumb').textContent=titles[target]+' · 加载中…';
  try {
    // A valid in-memory snapshot avoids fetching every account/group on each click.
    // Dedicated page results are always read, and expired state reads run in parallel.
    const [,data]=await Promise.all([
      !state || Date.now()-stateReadAt>=STATE_MAX_AGE_MS ? readState() : Promise.resolve(),
      readPageData(target,url),
    ]);
    if(revision!==renderRevision)return;
    page=target;
    readViewRoute(url);
    await render({revision,data});
    if(revision!==renderRevision)return;
    if(!pop)history.pushState(null,'',url.pathname+url.search);
    displayedConsoleUrl=url.pathname+url.search;
    navigationSucceeded=true;
    content.focus({preventScroll:true});
    window.scrollTo(...(pageScroll.get(target)||[0,0]));
  } catch(error) {
    if(revision!==renderRevision)return;
    page=previousPage;updateChrome();toast(error.message);
    if(pop)history.replaceState(null,'',displayedConsoleUrl);
  } finally {
    finishTiming(navigationSucceeded);
    if(revision===renderRevision){navigationPending=false;content.inert=false;content.removeAttribute('aria-busy');if(state)serviceHeader();}
  }
}
document.addEventListener('click',e=>{
  if(e.defaultPrevented || e.button!==0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey)return;
  const link=e.target.closest('a[href]');
  if(!link || link.target || link.hasAttribute('download'))return;
  const url=new URL(link.href,location.href);
  if(url.origin!==location.origin || url.hash || !/^\/console(?:\/[a-z]+)?\/?$/.test(url.pathname))return;
  const target=url.pathname.split('/')[2]||'overview';
  if(!Object.hasOwn(titles,target))return;
  e.preventDefault();navigateConsole(url);
});
window.addEventListener('popstate',()=>navigateConsole(new URL(location.href),{pop:true}));

let aiActivityRevision=0;
function applyAiSettings(saved, form, submittedKey) {
  if(!form.isConnected)return;
  $('#ai-saved-overview').innerHTML=aiSavedOverview(saved);
  $('#ai-plan-status').innerHTML=badge(saved.enabled&&saved.trigger!=='manual'?'active':'disabled',saved.trigger==='manual'?'仅手动生成':saved.enabled?'自动摘要已启用':'自动摘要已暂停');
  const key=form.querySelector('[name=api_key]');
  if(key.value===submittedKey)key.value='';
  key.placeholder=saved.api_key_configured?'已保存，留空保持不变':'请输入 API Key';
  form.querySelector('.connection-settings summary').textContent='AI 连接设置 · '+(saved.api_key_configured?'已保存凭据':'待配置');
  $('#preview-ai-summary').disabled=!saved.configured;
  $('#run-ai-summary').disabled=!saved.configured;
  const revision=++aiActivityRevision;
  api('/ai/activity').then(activity=>{
    if(revision!==aiActivityRevision || !form.isConnected)return;
    $('.summary-status').innerHTML=`<span>范围内已收集 <strong>${activity.collected}</strong> 条消息</span><span>${activity.next_at?'下次计划：'+new Date(activity.next_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+'（北京时间）':'当前未安排每日推送'}</span>`;
  }).catch(error=>{if(revision===aiActivityRevision&&form.isConnected)toast('设置已保存，运行状态刷新失败：'+error.message);});
}
async function applyChannelFeed(saved) {
  const index=channelFeeds.findIndex(f=>f.id===saved.id);
  if(index<0)channelFeeds.unshift(saved);else channelFeeds[index]=saved;
  if(page!=='channels')return;
  const html=await channels({data:[channelFeeds,channelResult]});
  replaceRegion('#channel-feeds-panel',html);
  const select=$('#channel-feed-filter');
  select.innerHTML='<option value="">全部来源</option>'+channelFeeds.map(f=>`<option value="${f.id}">${E(f.name)}</option>`).join('');
  select.value=channelFeedFilter;
  // Saving can cancel queued posts. Refresh those results without delaying the
  // saved settings or recreating unaffected page/form elements.
  const revision=++channelRefreshRevision, view=renderRevision, query=channelPath();
  api(query).then(async result=>{
    if(revision!==channelRefreshRevision || view!==renderRevision || page!=='channels' || $('#modal').open)return;
    const refreshed=await channels({data:[channelFeeds,result]});
    replaceRegion('#channel-posts-panel',refreshed);
  }).catch(error=>{if(view===renderRevision&&page==='channels')toast('设置已保存，帖子刷新失败：'+error.message);});
}
async function updateChannels(query) {
  const revision=++renderRevision;
  const controls=$$('#channel-posts-panel button, #channel-posts-panel select').map(e=>[e,e.disabled]);
  controls.forEach(([e])=>e.disabled=true);
  try {
    const data=await Promise.all([api('/channel-feeds'),api(channelPath(query))]);
    if(revision!==renderRevision || page!=='channels')return;
    channelFilter=query.status;channelFeedFilter=query.feed;channelOffset=query.offset;
    const html=await channels({data});
    replaceRegion('#channel-feeds-panel',html);
    replaceRegion('#channel-posts-panel',html);
  } catch(error) {
    if(revision!==renderRevision || page!=='channels')return;
    $('#channel-status').value=channelFilter;$('#channel-feed-filter').value=channelFeedFilter;
    toast(error.message);
  } finally { controls.forEach(([e,disabled])=>{if(e.isConnected)e.disabled=disabled;}); }
}
