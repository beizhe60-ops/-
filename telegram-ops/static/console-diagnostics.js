/* Read-only, on-demand diagnostics. Opening this view never probes or sends to Telegram. */
const listenerOutcomes={received:'已收到消息',resolving_sender:'正在识别发言者',matched:'已通过关键词筛选，请查看发送记录',own_account_or_non_user:'已跳过：本系统账号或非个人账号',message_too_long:'已跳过：转发内容超长',handler_error:'处理异常，请查看服务日志',empty_text:'已跳过：没有文字内容',service_paused:'已跳过：服务已暂停',not_group:'已跳过：不是群组或频道消息',missing_target:'已跳过：尚未设置转发目标',outside_monitor_scope:'已跳过：来源不在账号监测范围',outgoing_message:'已跳过：监测账号自己发送的消息'};
let diagnosticRevision=0;
function diagnosticContent(task, status) {
  const events=(status.events||[]).filter(e=>e.task_id===task.id || (e.task_id==null&&task.source_chats.includes(e.chat_id)));
  const received=events.filter(e=>e.outcome==='received').at(-1);
  const account=state.accounts.find(a=>a.id===task.account_a);
  const warnings=[!state.running&&'后台服务当前已暂停',!task.enabled&&'此任务当前已暂停',!task.relay_chat&&'尚未设置转发目标群',!status.connected&&'监测账号当前未连接',status.update_loop_running===false&&'消息监听连接异常',account?.send_enabled===false&&'监测账号发送权限已关闭'].filter(Boolean);
  const polling=status.polling;
  const pollLabels={baseline:'已建立新消息起点',checked:'已检查新消息',platform_wait:'等待 Telegram 限制恢复',error:'最近一次补查失败'};
  return `<div class="diagnostic-summary"><div><span>最近收到来源群消息</span><strong>${E(received?date(received.at):'近期暂无接收记录')}</strong></div><div><span>账号连接</span>${badge(status.connected?'active':'connection_error',status.connected?'已连接':'未连接')}</div></div>${warnings.length?`<div class="callout">${warnings.map(E).join('；')}。配置状态仅表示当前情况。</div>`:''}<p class="page-note">仅显示本次服务运行中、账号最近 100 条诊断事件里与此任务相关的部分；一条消息可能有多个处理步骤。没有近期记录不代表该群不可监测。</p>
    <div class="diagnostic-events">${[...events].reverse().map(e=>`<article><span>${E(date(e.at))}</span><div><strong>${E(listenerOutcomes[e.outcome]||e.outcome)}</strong><small>${E(chatName(e.chat_id))} · 消息 ${e.message_id}</small></div></article>`).join('')||empty('暂无相关诊断','任务启用后，用其他账号在来源群发一条新消息，再点“刷新诊断”。')}</div>
    ${polling?`<p class="page-note">账号最近补查：${E(date(polling.at))} · ${E(pollLabels[polling.outcome]||polling.outcome)}${polling.chat_id?' · '+E(chatName(polling.chat_id)):''}。这条状态属于账号整体。</p>`:''}`;
}
function taskDiagnostics(id) {
  const task=state.tasks.find(t=>t.id===id);if(!task)return;
  openModal('任务诊断 · '+task.name,`<div class="actions diagnostic-actions"><button type="button" class="secondary" id="refresh-diagnostics">刷新诊断</button><a class="button secondary" href="/console/records?task_id=${task.id}">查看此任务记录</a></div><div id="diagnostic-body" role="status">正在读取近期诊断…</div>`);
  const body=$('#diagnostic-body'),button=$('#refresh-diagnostics');
  async function refresh(){
    const revision=++diagnosticRevision;button.disabled=true;
    try{const status=await api(`/accounts/${task.account_a}/listener`);if(revision===diagnosticRevision&&body.isConnected)body.innerHTML=diagnosticContent(task,status);}
    catch(error){if(body.isConnected)body.textContent='诊断读取失败：'+error.message;}
    finally{if(body.isConnected)button.disabled=false;}
  }
  button.onclick=refresh;refresh();
}
