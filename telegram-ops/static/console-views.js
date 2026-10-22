/* Page structure only. All writes remain in the existing console handlers. */
const pageSections = {tasks:'relay',ai:'results',channels:'results'};
const sectionChoices = {tasks:['relay','private'],ai:['results','settings'],channels:['results','settings']};
const taskAccountFilters = {relay:'',private:''};
let recordItems = [];
function readViewRoute(url) {
  const target=url.pathname.split('/')[2];
  const view=url.searchParams.get('view');
  if(sectionChoices[target]?.includes(view))pageSections[target]=view;
  if(target==='tasks' && url.searchParams.has('account')) {
    const id=url.searchParams.get('account');
    taskAccountFilters[pageSections.tasks]=/^\d+$/.test(id)?id:'';
  }
}
function sectionTabs(module, items) {
  return `<div class="section-tabs" role="tablist" aria-label="${E(titles[module])}视图">${items.map(([key,label])=>`<button type="button" role="tab" id="${module}-tab-${key}" aria-controls="${module}-view-${key}" aria-selected="${pageSections[module]===key}" tabindex="${pageSections[module]===key?0:-1}" data-section-module="${module}" data-section="${key}">${label}</button>`).join('')}</div>`;
}
function sectionPanel(module,key) {
  return `id="${module}-view-${key}" role="tabpanel" aria-labelledby="${module}-tab-${key}" ${pageSections[module]===key?'':'hidden'}`;
}
function selectSection(module,key,{url=true}={}) {
  if(!sectionChoices[module]?.includes(key))return;
  pageSections[module]=key;
  $$(`[data-section-module="${module}"]`).forEach(b=>{const selected=b.dataset.section===key;b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1;});
  for(const value of sectionChoices[module]) {
    const panel=$(`#${module}-view-${value}`);if(panel)panel.hidden=value!==key;
  }
  if(module==='ai'&&key==='settings')bindAiSettings();
  if(url&&page===module){const next=new URL(location.href);next.searchParams.set('view',key);if(module==='tasks'){const account=taskAccountFilters[key];if(account)next.searchParams.set('account',account);else next.searchParams.delete('account');}displayedConsoleUrl=next.pathname+next.search;history.replaceState(null,'',displayedConsoleUrl);}
}
function bindSections() {
  $$('[data-section-module]').forEach(b=>{
    b.onclick=()=>selectSection(b.dataset.sectionModule,b.dataset.section);
    b.onkeydown=e=>{const group=$$(`[data-section-module="${b.dataset.sectionModule}"]`),index=group.indexOf(b);let next;
      if(e.key==='ArrowRight')next=(index+1)%group.length;
      else if(e.key==='ArrowLeft')next=(index+group.length-1)%group.length;
      else if(e.key==='Home')next=0;else if(e.key==='End')next=group.length-1;else return;
      e.preventDefault();group[next].click();group[next].focus();
    };
  });
  $$('[data-open-section]').forEach(b=>b.onclick=()=>selectSection(page,b.dataset.openSection));
}
function accountTaskLink(a) {
  return `/console/tasks?view=${a.role==='sender'?'private':'relay'}&account=${a.id}`;
}
function taskAccountSelect(kind) {
  const role=kind==='relay'?'monitor':'sender';
  return `<label class="inline-filter">筛选账号<select data-task-account="${kind}" aria-label="${kind==='relay'?'监测':'私信'}任务账号"><option value="">全部${role==='monitor'?'监测':'私信'}账号</option>${state.accounts.filter(a=>a.role===role).map(a=>`<option value="${a.id}" ${String(a.id)===taskAccountFilters[kind]?'selected':''}>${E(a.name)}</option>`).join('')}</select></label>`;
}
function visibleRelayTasks() {
  return state.tasks.filter(t=>!taskAccountFilters.relay||String(t.account_a)===taskAccountFilters.relay);
}
function senderTasks() {
  const items=state.accounts.filter(a=>a.role==='sender'&&(!taskAccountFilters.private||String(a.id)===taskAccountFilters.private));
  return `<section class="panel" id="private-tasks-panel"><div class="panel-head"><div><h2>私信接收与文案</h2><p class="subtle">接收监测转发的线索，按账号权重分配私信。</p></div>${taskAccountSelect('private')}</div>${items.length?items.map(a=>`<article class="sender-task task-row" data-sender-task="${a.id}"><div><div class="actions"><strong>${E(a.name)}</strong>${badge(a.connected?'active':a.status,a.connected?'已连接':undefined)}</div><p class="task-flow">${(a.receive_chat_ids||[]).length} 个接收群 · 轮询权重 ${a.rotation_weight??1} · ${a.send_enabled?'允许发送':'发送已关闭'}</p><div class="chips">${(a.receive_chat_ids||[]).map(id=>`<span class="chip" title="${id}">${E(chatName(id))}</span>`).join('')||'<span class="muted">尚未绑定接收群</span>'}</div></div><div class="actions"><button class="secondary small" data-receive-groups="${a.id}">接收群组与私信文案</button><button class="secondary small" data-weight="${a.id}">设置轮询权重</button><a class="text-button small" href="/console/accounts">账号管理</a></div></article>`).join(''):empty('暂无私信账号','登录一个私信账号后，在这里设置接收群组、文案和分配权重。','<a class="button secondary" href="/console/accounts">前往账号管理</a>')}</section><p class="page-note">只接收已绑定群内、本系统监测账号转发的消息。修改文案或接收范围会取消受影响的待发送私信。</p>`;
}
function plainMessage(value) {
  // Parse in an inert template, then render text with escaping; no untrusted markup enters the live DOM.
  const text=String(value??'');
  if(!/<\/?(?:a|b|strong|i|em|u|s|strike|code|pre|blockquote|tg-spoiler)(?:\s|>)/i.test(text))return text;
  const t=document.createElement('template');t.innerHTML=text;
  t.content.querySelectorAll('script,style').forEach(el=>el.remove());
  return t.content.textContent||'';
}
function recordDialog(id) {
  const item=recordItems.find(r=>r.id===id);if(!item)return;
  const plain=plainMessage(item.text);
  openModal('消息详情',`<div class="record-detail"><div class="actions">${badge(item.status)}<span class="muted">${E(date(item.created_at))}</span></div><dl class="detail-meta"><div><dt>发送类型</dt><dd>${item.stage==='relay'?'群组转发':'自动私信'}</dd></div><div><dt>用户</dt><dd>@${E(item.username)}${item.user_id?` · ${E(item.user_id)}`:''}</dd></div><div><dt>来源群</dt><dd>${E(item.source_title||'—')}<small>${E(item.chat_id)}</small></dd></div><div><dt>所属任务</dt><dd>${E(state.tasks.find(t=>t.id===item.task_id)?.name||`任务 #${item.task_id}`)}</dd></div></dl>${item.error?`<div class="callout record-error">${E(item.error)}</div>`:''}${item.status==='waiting'?`<p class="page-note">等待至 ${E(date(item.due_at))}</p>`:''}<h3>发送内容</h3><div class="message-body">${E(plain)}</div>${plain!==item.text?`<details class="record-source"><summary>查看原始发送格式</summary><pre class="message-body">${E(item.text)}</pre></details>`:''}<h3>原始发言</h3><div class="message-body">${E(item.original_text||'—')}</div></div>`);
}
function bindStructure() {
  bindSections();
  $$('[data-task-account]').forEach(select=>select.onchange=()=>{
    taskAccountFilters[select.dataset.taskAccount]=select.value;
    selectSection('tasks',select.dataset.taskAccount);render();
  });
  $$('[data-record-detail]').forEach(b=>b.onclick=()=>recordDialog(Number(b.dataset.recordDetail)));
  $('#record-search')?.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();$('#search-records').click();}});
}

function aiSavedOverview(ai) {
  const target=ai.target_type==='private'?`私人账号 ${ai.target_id||'待配置'}`:ai.target_id?chatName(ai.target_id):'待配置';
  return `<dl class="saved-plan"><div><dt>分析来源</dt><dd>${(ai.source_chat_ids||[]).length} 个群组<small>${ai.account_id?E(accountName(ai.account_id)):'待选择账号'}</small></dd></div><div><dt>摘要范围</dt><dd>最近 ${ai.lookback_hours||24} 小时<small>最多 ${ai.message_count||50} 条消息</small></dd></div><div><dt>触发方式</dt><dd>${ai.trigger==='manual'?'手动生成':ai.trigger==='count'?`每新增 ${ai.trigger_count||50} 条`:`每天 ${E(ai.schedule_time||'23:00')}`}<small>${ai.trigger==='schedule'?'北京时间':'按已保存规则执行'}</small></dd></div><div><dt>推送目标</dt><dd>${E(target)}</dd></div></dl>`;
}
function aiSummaryStatus(activity) {
  return `<span>范围内已收集 <strong>${activity.collected}</strong> 条消息</span><span>${activity.next_at?'下次计划：'+new Date(activity.next_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+'（北京时间）':'当前未安排每日推送'}</span>`;
}
function aiRunRows(activity) {
  return activity.runs.length?`<div class="activity-list">${activity.runs.map(r=>`<article class="activity-row"><div class="activity-time">${E(date(r.created_at))}</div><div>${badge(r.status,r.status==='preview'?'已生成预览':r.status==='sent'?'推送成功':r.status==='unknown'?'结果待核实':'生成 / 推送失败')}<p class="activity-detail">${E(r.detail)}</p></div><span class="muted">${r.message_count||0} 条消息</span></article>`).join('')}</div>`:empty('还没有运行记录','先在分析设置中选择群组和接收人，再预览摘要。','<button class="secondary" data-open-section="settings">前往分析设置</button>');
}
async function refreshAiActivity() {
  const marker=$('#ai-activity-list'),revision=++aiActivityRevision;
  const activity=await api('/ai/activity');
  if(revision!==aiActivityRevision||!marker?.isConnected)return;
  marker.innerHTML=aiRunRows(activity);
  $('.summary-status').innerHTML=aiSummaryStatus(activity);
  bindSections();
}
