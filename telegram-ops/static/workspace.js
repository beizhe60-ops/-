/* Workflow UI. Uses the shared console API, escaping and modal helpers. */
function workspaceHome() {
  const connected=state.accounts.filter(a=>a.connected).length;
  const offline=state.accounts.filter(a=>!a.connected);
  return heading('工作台','先查看运行情况，再进入需要处理的任务。','<a class="button secondary" href="/console/records">查看消息记录</a>')+
    `<section class="home-status" aria-label="当前运行概况"><div><span class="status-dot ${state.running?'online':''}"></span><strong>${state.running?'后台服务运行中':'后台服务已暂停'}</strong><small>关闭网页不影响后台任务</small></div><a href="/console/accounts"><strong>${connected} / ${state.accounts.length}</strong><span>账号已连接</span></a><a href="/console/tasks"><strong>${state.tasks.filter(t=>t.enabled).length}</strong><span>监测任务已启用</span></a><a href="/console/records"><strong>${state.stats.pending||0}</strong><span>转发 / 私信待处理</span></a></section>
    ${(offline.length||state.stats.failed||!state.application?.configured)?`<section class="attention-list" aria-label="需要关注"><strong>需要关注</strong>${!state.application?.configured?'<a href="/console/settings">完成 Telegram 连接配置</a>':''}${offline.length?`<a href="/console/accounts">${offline.length} 个账号尚未连接，查看账号状态</a>`:''}${state.stats.failed?`<a href="/console/records">${state.stats.failed} 条转发或私信失败 / 待核实，查看记录</a>`:''}</section>`:''}
    <div class="section-heading"><h2>常用功能</h2><span class="subtle">按要做的事情进入</span></div><section class="workflow-menu">${[
      ['broadcasts','群发任务','编辑通知，选择多个接收群，再预览发送。','↗'],
      ['tasks','监测转发','管理关键词转发，以及私信接收和文案。','⇄'],
      ['channels','频道采编','查看采集结果，筛选、排版并发布频道内容。','▤'],
      ['ai','AI 群组分析','查看摘要结果，设置分析范围和推送计划。','✦']
    ].map(([path,title,desc,icon])=>`<a class="workflow-entry" href="/console/${path}"><span class="workflow-icon" aria-hidden="true">${icon}</span><div><h2>${title}</h2><p>${desc}</p></div><span aria-hidden="true">›</span></a>`).join('')}</section>
    <section class="home-resources"><a href="/console/accounts"><strong>账号管理</strong><span>登录、连接状态与发送权限</span></a><a href="/console/records"><strong>消息记录</strong><span>查找结果与失败原因</span></a><a href="/console/settings"><strong>系统设置</strong><span>Telegram 连接与后台密码</span></a></section>
    <details class="workflow-help" ${!state.accounts.length?'open':''}><summary>首次使用流程</summary><ol class="onboarding-steps"><li><strong>登录账号</strong><p>在账号管理完成登录，各项功能共用账号。</p></li><li><strong>配置任务</strong><p>选择群发、监测转发、频道采编或 AI 分析。</p></li><li><strong>核对后启用</strong><p>先预览或测试，再启用所需任务。右上角“暂停全部”会停止所有模块。</p></li></ol></details>`;
}

function groupPicker(name, accountId, selected = [], single = false) {
  return `<fieldset class="group-picker" data-picker="${name}" data-account="${accountId || ''}" data-single="${single}"><legend>${single ? "选择目标群" : "选择群组"}</legend><div class="actions"><input type="search" data-group-search placeholder="搜索已同步群名称或 ID" aria-label="搜索群组"><button type="button" class="secondary small" data-sync-picker>更新群组列表</button></div><div class="group-options"></div><details ${selected.some(id=>!state.chats.some(c=>c.account_id==accountId && c.id==id)) ? 'open' : ''}><summary>高级设置：手动填写群组 ID</summary><label>群组 ID${single ? `<input name="${name}" value="${E(selected[0] || '')}" placeholder="完整负数 ID">` : `<textarea name="${name}" rows="2" placeholder="每行一个完整负数 ID">${E(selected.join('\n'))}</textarea>`}</label></details><small data-selected-count></small></fieldset>`;
}
function bindPicker(picker, accountSelector) {
  const input = picker.querySelector('[name]');
  function paint() {
    const aid = Number($(accountSelector)?.value || picker.dataset.account);
    picker.dataset.account = String(aid);
    const search = picker.querySelector('[data-group-search]').value.toLowerCase();
    const selected = input.value.split(/[\s,，]+/).map(Number);
    const groups = state.chats.filter(c=>c.account_id===aid && c.id<0);
    picker.querySelector('.group-options').innerHTML = groups.filter(c=>(c.title+' '+c.id).toLowerCase().includes(search)).map(c=>`<label class="group-option"><input type="${picker.dataset.single==='true'?'radio':'checkbox'}" name="pick-${picker.dataset.picker}" value="${c.id}" ${selected.includes(c.id)?'checked':''}><span>${E(c.title)}<small>${c.id}</small></span></label>`).join('') || '<p class="subtle">暂无匹配群组。点击“更新群组列表”，或展开高级设置填写 ID。</p>';
    picker.querySelectorAll('.group-options input').forEach(box=>box.onchange=()=>{
      let ids=input.value.split(/[\s,，]+/).filter(Boolean);
      if(picker.dataset.single==='true') ids=[box.value];
      else ids=box.checked?[...new Set([...ids,box.value])]:ids.filter(x=>x!==box.value);
      input.value=ids.join('\n'); count();
    }); count();
  }
  function count(){picker.querySelector('[data-selected-count]').textContent='已选择 '+input.value.split(/[\s,，]+/).filter(Boolean).length+' 个群组；账号需已加入并拥有相应权限。';}
  let searchTimer;
  picker.querySelector('[data-group-search]').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{if(picker.isConnected)paint();},120);};
  input.oninput=paint;
  const account = $(accountSelector);
  account?.addEventListener('change',()=>{ input.value=''; paint(); });
  picker.querySelector('[data-sync-picker]').onclick=async e=>{
    const button=e.currentTarget; button.disabled=true;
    try{await api(`/accounts/${Number(account?.value || picker.dataset.account)}/sync`, 'POST'); await readState({fresh:true}); paint(); toast('群组列表已更新');}
    catch(ex){toast(ex.message);}finally{button.disabled=false;}
  };
  paint();
}

let broadcastItems=[];
const campaignNames={draft:'草稿',queued:'待发送 / 发送中',paused:'已暂停',completed:'已处理完成',cancelled:'已取消'};
async function broadcasts({data}={}) {
  broadcastItems=data ?? await api('/broadcasts');
  return heading('群发任务','编辑一条通知，发送到选定的多个群。每个任务只发送一轮。','<button id="new-broadcast">＋ 新建群发</button>')+
    (!state.running?'<div class="callout">服务已暂停。可以保存和预览任务，启动服务后才会发送。</div>':'')+
    `<section class="panel"><div class="panel-head"><h2>最近群发任务</h2><button class="secondary small" id="refresh-workspace">刷新状态</button></div>${broadcastItems.length?broadcastItems.map(t=>`<article class="task-row"><div><div class="actions"><strong>${E(t.name)}</strong>${badge(t.status,campaignNames[t.status])}</div><p>${E(accountName(t.account_id))} · ${t.targets.length} 个群 · ${t.scheduled_at?date(t.scheduled_at):'启动后发送'}</p><p class="subtle">成功 ${t.counts.sent||0} · 待发送 ${(t.counts.pending||0)+(t.counts.waiting||0)} · 失败 / 待核实 ${(t.counts.failed||0)+(t.counts.unknown||0)}</p></div><div class="actions"><button class="secondary small" data-campaign-preview="${t.id}">查看内容</button><button class="secondary small" data-campaign-records="${t.id}">发送记录</button>${['draft','paused'].includes(t.status)?`<button class="small" data-campaign-start="${t.id}">${t.status==='draft'?'预览并启动':'继续发送'}</button>`:''}${t.status==='queued'?`<button class="secondary small" data-campaign-pause="${t.id}">暂停</button>`:''}${['draft','paused','queued'].includes(t.status)?`<button class="text-button small" data-campaign-cancel="${t.id}">取消任务</button>`:''}</div></article>`).join(''):empty('创建第一条群发通知','选择发送账号和接收群，预览内容后再启动。')}</section><p class="subtle">暂停或取消只影响尚未发送的内容；发送结果不确定时会保留记录，不自动重发。</p>`;
}
function broadcastDialog(){
  const accounts=state.accounts.filter(a=>a.has_session);
  if(!accounts.length){toast('请先在账号管理登录一个 Telegram 账号');return;}
  const requestKey=Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
  openModal('新建群发',`<form id="broadcast-form"><label>任务名称<input name="name" required maxlength="120" placeholder="例如：周末活动通知"></label><div class="form-section"><h3>1. 编辑通知</h3><label>发送账号<select name="account_id">${accounts.map(a=>`<option value="${a.id}">${E(a.name)}</option>`).join('')}</select></label><label>通知内容<textarea name="text" rows="5" maxlength="3500" required placeholder="输入要发送的文字通知"></textarea></label></div><div class="form-section"><h3>2. 选择接收群</h3>${groupPicker('targets',accounts[0].id)}</div><div class="form-section"><h3>3. 发送时间</h3><label>发送方式<select name="timing"><option value="now">启动后发送</option><option value="scheduled">指定时间发送</option></select></label><label id="campaign-time" hidden>发送时间（北京时间）<input type="datetime-local" name="scheduled_at"></label></div><div class="form-error" role="alert"></div><div class="form-footer"><button type="submit">保存草稿并预览</button></div></form>`);
  bindPicker($('[data-picker=targets]'),'#broadcast-form [name=account_id]');
  $('#broadcast-form [name=timing]').onchange=e=>{$('#campaign-time').hidden=e.target.value!=='scheduled';$('#campaign-time input').required=e.target.value==='scheduled';};
  setupForm('#broadcast-form',async fd=>{
    const result=await api('/broadcasts','POST',{request_key:requestKey,name:fd.get('name'),account_id:Number(fd.get('account_id')),targets:parseGroupIds(fd.get('targets')),text:fd.get('text'),scheduled_at:fd.get('timing')==='scheduled'?fd.get('scheduled_at')+':00+08:00':null});
    await load({refreshState:false}); campaignPreview(result,true);
  });
}
function campaignPreview(t, start=false){
  openModal(start?'确认群发内容':'群发内容',`<p><strong>${E(t.name)}</strong></p><p>发送账号：${E(accountName(t.account_id))}</p><p>接收群：${t.targets.map(id=>E(chatName(id))+' ('+id+')').join('、')}</p><p>发送时间：${t.scheduled_at?date(t.scheduled_at):'启动后发送'}</p><div class="preview-box">${E(t.text)}</div><p class="subtle">${state.running?'启动后将按任务时间逐群发送。':'服务已暂停；任务启动后等待服务恢复。'}</p>${start?'<div class="form-error" role="alert" id="campaign-error"></div><div class="form-footer"><button id="launch-campaign">确认启动群发</button></div>':''}`);
  if(start)$('#launch-campaign').onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{await api(`/broadcasts/${t.id}/start`,'POST');$('#modal').close();await load({refreshState:false});toast('群发任务已启动');}catch(ex){$('#campaign-error').textContent=ex.message;b.disabled=false;}};
}

async function workspaceAi(data){
  const [ai,activity]=data ?? await Promise.all([api('/ai/settings'),api('/ai/activity')]);
  const accounts=state.accounts.filter(a=>a.role==='monitor'&&a.has_session);
  const aid=ai.account_id||accounts[0]?.id;
  return heading('AI 群组分析','查看摘要执行结果，或到分析设置调整群组与推送计划。',`<span id="ai-plan-status">${badge(ai.enabled&&ai.trigger!=='manual'?'active':'disabled',ai.trigger==='manual'?'仅手动生成':ai.enabled?'自动摘要已启用':'自动摘要已暂停')}</span>`)+sectionTabs('ai',[['results','分析结果'],['settings','分析设置']])+
  `<div ${sectionPanel('ai','results')}><section class="panel"><div class="panel-head"><h2>当前摘要计划</h2><button class="secondary small" data-open-section="settings">编辑设置</button></div><div id="ai-saved-overview">${aiSavedOverview(ai)}</div><div class="panel-body ai-result-actions"><div class="summary-status">${aiSummaryStatus(activity)}</div>${!ai.configured?'<p class="page-note">完成分析设置后，即可预览和推送。</p>':''}${!state.running?'<p class="page-note">服务已暂停，自动收集和推送将在启动服务后恢复。</p>':''}<div class="actions"><button type="button" class="secondary" id="preview-ai-summary" ${!ai.configured?'disabled':''}>预览摘要</button><button type="button" id="run-ai-summary" ${!ai.configured?'disabled':''}>立即生成并推送</button></div><p class="page-note">使用已保存的设置。预览不向 Telegram 发消息；调用 AI 会使用接口额度。</p><p id="ai-run-result" role="status"></p></div></section><section class="panel"><div class="panel-head"><h2>摘要运行记录</h2><button class="secondary small" id="refresh-ai-activity">刷新记录</button></div><div id="ai-activity-list">${aiRunRows(activity)}</div></section></div><div ${sectionPanel('ai','settings')}>`+
  `<section class="panel ai-settings-panel"><div class="panel-head"><h2>摘要计划</h2><span class="muted">保存后生效</span></div><div class="panel-body"><form id="ai-form"><div class="form-section"><h3>1. 选择分析群组</h3><label>收集与推送账号<select name="account_id" required>${accounts.map(a=>`<option value="${a.id}" ${a.id===aid?'selected':''}>${E(a.name)}</option>`).join('')||'<option value="">请先登录监测账号</option>'}</select></label>${groupPicker('source_chat_ids',aid,ai.source_chat_ids||[])}</div><div class="form-section"><h3>2. 设置摘要规则</h3><div class="form-grid"><label>什么时候生成<select name="trigger"><option value="schedule" ${ai.trigger==='schedule'?'selected':''}>每天定时生成</option><option value="count" ${ai.trigger==='count'?'selected':''}>按新增消息数量生成</option><option value="manual" ${ai.trigger==='manual'?'selected':''}>仅手动生成</option></select></label><label data-schedule-field>每日推送时间（北京时间）<input name="schedule_time" type="time" value="${E(ai.schedule_time||'23:00')}"></label><label data-count-field>每新增多少条消息生成一次<input name="trigger_count" type="number" min="1" max="1000" value="${ai.trigger_count||50}"></label><label>每次最多分析多少条消息<input name="message_count" type="number" min="1" max="1000" required value="${ai.message_count||50}"></label><label>分析最近多久的聊天（小时）<input name="lookback_hours" type="number" min="1" max="168" required value="${ai.lookback_hours||24}"></label></div><small>只分析本账号在所选群中收集的新消息；超过上限时取最近的消息。</small></div><div class="form-section"><h3>3. 选择摘要接收人</h3><label>发送到<select name="target_type"><option value="group" ${ai.target_type!=='private'?'selected':''}>群组</option><option value="private" ${ai.target_type==='private'?'selected':''}>私人账号</option></select></label><div id="ai-target-group">${groupPicker('target_group',aid,ai.target_type!=='private'&&ai.target_id?[ai.target_id]:[],true)}</div><label id="ai-target-private">私人 Telegram 用户 ID<input name="target_private" inputmode="numeric" value="${ai.target_type==='private'?E(ai.target_id):''}" placeholder="完整正整数用户 ID"><small>发送账号需能访问该用户，且对方允许接收消息。</small></label></div>
  <details class="connection-settings" ${!ai.api_key_configured?'open':''}><summary>AI 连接设置 · ${ai.api_key_configured?'已保存凭据':'待配置'}</summary><p class="subtle">首次填写或更换服务商时设置。连接测试使用测试文字，不读取群聊。</p><label>服务商<select id="ai-provider"><option value="custom">自定义 / 当前地址</option><option value="quickrouter">QuickRouter</option><option value="openai">OpenAI</option></select></label><div class="form-grid"><label>接口地址<input name="api_url" required type="url" value="${E(ai.api_url||'https://api.quickrouter.ai/v1/chat/completions')}"></label><label>模型 ID<input name="model" required value="${E(ai.model||'')}" placeholder="填写服务商支持的准确模型 ID"></label><label class="span-2">API Key<input name="api_key" type="password" autocomplete="new-password" placeholder="${ai.api_key_configured?'已保存，留空保持不变':'填写对应服务商的 API Key'}"></label></div><button type="button" class="secondary" id="test-ai-connection">测试 AI 连接</button><p id="ai-test-result" role="status"></p></details><label class="checkbox-label"><input type="checkbox" name="enabled" ${ai.enabled?'checked':''}>启用自动摘要</label><div class="form-error" role="alert"></div><div class="form-footer sticky-form-footer"><span class="subtle">修改不会自动保存</span><button type="submit">保存摘要设置</button></div></form></div></section></div>`;
}

function bindWorkspace(){
  if($('#new-broadcast'))$('#new-broadcast').onclick=broadcastDialog;
  if($('#refresh-workspace'))$('#refresh-workspace').onclick=e=>action(e.currentTarget,async()=>{},'已刷新');
  $$('[data-campaign-preview]').forEach(b=>b.onclick=()=>campaignPreview(broadcastItems.find(t=>t.id===Number(b.dataset.campaignPreview))));
  $$('[data-campaign-start]').forEach(b=>b.onclick=()=>campaignPreview(broadcastItems.find(t=>t.id===Number(b.dataset.campaignStart)),true));
  for(const name of ['pause','cancel'])$$(`[data-campaign-${name}]`).forEach(b=>b.onclick=()=>{if(name==='cancel'&&!confirm('取消尚未发送的内容？已经发送的消息会保留。'))return;action(b,()=>api(`/broadcasts/${b.getAttribute('data-campaign-'+name)}/${name}`,'POST'),'群发状态已更新');});
  $$('[data-campaign-records]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{const rows=await api(`/broadcasts/${b.dataset.campaignRecords}/records`);openModal('逐群发送记录',rows.map(r=>`<div class="task-row"><div><strong>${E(chatName(r.chat_id))}</strong><p>${r.chat_id} · ${E(labels[r.status]||r.status)}</p><p>${E(r.error||'')}</p><small>${date(r.sent_at)}</small></div></div>`).join('')||'<p>任务仍为草稿，尚未开始发送。</p>');}catch(ex){toast(ex.message);}finally{b.disabled=false;}});
  if(!$('#ai-form'))return;
  bindAiActions();
  if(pageSections.ai==='settings')bindAiSettings();
}
function bindAiSettings(){
  const form=$('#ai-form');if(!form||form.dataset.bound)return;
  form.dataset.bound='true';
  bindPicker($('[data-picker=source_chat_ids]'),'#ai-form [name=account_id]');
  bindPicker($('[data-picker=target_group]'),'#ai-form [name=account_id]');
  const update=()=>{const trigger=$('#ai-form [name=trigger]').value; $('#ai-form [name=enabled]').disabled=trigger==='manual'; $('[data-schedule-field]').hidden=trigger!=='schedule';$('[data-count-field]').hidden=trigger!=='count';const privateTarget=$('#ai-form [name=target_type]').value==='private';$('#ai-target-private').hidden=!privateTarget;$('#ai-target-group').hidden=privateTarget;};
  $('#ai-form [name=trigger]').onchange=update;$('#ai-form [name=target_type]').onchange=update;update();
  $('#ai-provider').onchange=e=>{const urls={quickrouter:'https://api.quickrouter.ai/v1/chat/completions',openai:'https://api.openai.com/v1/chat/completions'};if(urls[e.target.value])$('#ai-form [name=api_url]').value=urls[e.target.value];};
  $('#test-ai-connection').onclick=async e=>{const b=e.currentTarget;b.disabled=true;$('#ai-test-result').textContent='正在测试连接…';try{const fd=new FormData($('#ai-form'));const r=await api('/ai/test','POST',{api_url:fd.get('api_url'),model:fd.get('model'),api_key:fd.get('api_key')});$('#ai-test-result').textContent=r.message;}catch(ex){$('#ai-test-result').textContent=ex.message;}finally{b.disabled=false;}};
  setupForm('#ai-form',async (fd,form)=>{const target=fd.get('target_type')==='private'?Number(fd.get('target_private')):parseGroupIds(fd.get('target_group'))[0];const saved=await api('/ai/settings','PUT',{enabled:fd.get('enabled')==='on',account_id:Number(fd.get('account_id')),source_chat_ids:parseGroupIds(fd.get('source_chat_ids')),api_url:fd.get('api_url').trim(),api_key:fd.get('api_key'),model:fd.get('model').trim(),trigger:fd.get('trigger'),target_type:fd.get('target_type'),target_id:target,schedule_time:fd.get('schedule_time')||'23:00',trigger_count:Number(fd.get('trigger_count')),message_count:Number(fd.get('message_count')),lookback_hours:Number(fd.get('lookback_hours'))});applyAiSettings(saved,form,fd.get('api_key'));toast('摘要设置已保存');});
}
function bindAiActions(){
  const refresh=$('#refresh-ai-activity');
  refresh.onclick=async()=>{refresh.disabled=true;try{await refreshAiActivity();toast('摘要记录已刷新');}catch(ex){toast(ex.message);}finally{refresh.disabled=false;}};
  for(const mode of ['preview','run']){
    const button=$(mode==='preview'?'#preview-ai-summary':'#run-ai-summary');
    button.onclick=async()=>{
      if(mode==='run'&&!confirm('按已保存的设置生成摘要并发送给接收人？'))return;
      const buttons=[$('#preview-ai-summary'),$('#run-ai-summary')];buttons.forEach(b=>b.disabled=true);$('#ai-run-result').textContent=mode==='preview'?'正在生成预览…':'正在生成并推送…';
      try{const r=await api('/ai/'+mode,'POST');$('#ai-run-result').textContent=r.preview?'预览已生成，尚未推送。':r.sent?`已推送摘要（${r.message_count} 条消息）`:r.reason;if(r.preview)openModal('摘要预览 · 未推送',`<p class="subtle">基于已保存设置，分析了 ${r.message_count} 条消息。</p><div class="preview-box">${E(r.preview)}</div>`);}
      catch(ex){$('#ai-run-result').textContent=ex.message;}finally{buttons.forEach(b=>b.disabled=false);refreshAiActivity().catch(error=>toast('摘要记录刷新失败：'+error.message));}
    };
  }
}
