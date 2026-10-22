/* Applied query state changes only after a successful read. */
let recordPeriod='all', recordTask='', recordResult=null;
function currentRecordQuery(){return {offset:recordOffset,status:recordStatus,search:recordSearch,period:recordPeriod,task:recordTask};}
function recordQueryFromUrl(url) {
  if(!url.search)return currentRecordQuery();
  const q=url.searchParams, task=q.get('task_id')||'';
  return {offset:Math.max(0,Number(q.get('offset'))||0),status:q.get('status')||'',search:q.get('q')||'',period:['today','7d'].includes(q.get('period'))?q.get('period'):'all',task:/^[1-9]\d*$/.test(task)?task:''};
}
function applyRecordQuery(query) {
  recordOffset=query.offset;recordStatus=query.status;recordSearch=query.search;
  recordPeriod=query.period??recordPeriod;recordTask=query.task??recordTask;
}
function recordQueryUrl(query=currentRecordQuery()) {
  const q=new URLSearchParams({status:query.status,q:query.search,offset:String(query.offset),period:query.period||'all'});
  if(query.task)q.set('task_id',query.task);return q.toString();
}
function rememberRecordUrl() {
  displayedConsoleUrl='/console/records?'+recordQueryUrl();history.replaceState(null,'',displayedConsoleUrl);
}
function recordExtraFilters(result, period, task) {
  const tasks=result.tasks||state.tasks;
  return `<select id="record-period" aria-label="记录时间范围"><option value="all" ${period==='all'?'selected':''}>全部时间</option><option value="today" ${period==='today'?'selected':''}>今天（北京时间）</option><option value="7d" ${period==='7d'?'selected':''}>最近 7 天</option></select><select id="record-task" aria-label="记录所属任务"><option value="">全部任务</option>${tasks.map(t=>`<option value="${t.id}" ${String(t.id)===task?'selected':''}>${E(t.name)}${t.deleted?'（已删除）':''}</option>`).join('')}${task&&!tasks.some(t=>String(t.id)===task)?`<option value="${E(task)}" selected>任务 ${E(task)}</option>`:''}</select>`;
}
function exceptionAttention() {
  const counts=state.stats.exceptions;
  if(!counts)return state.stats.failed?`<a href="/console/records?status=exceptions">累计 ${state.stats.failed} 条异常记录，查看明细</a>`:'';
  return [['failed','发送失败'],['unknown','结果待核实']].filter(([key])=>counts[key]).map(([key,label])=>`<a href="/console/records?status=${key}&period=all">累计 ${counts[key]} 条${label}，查看记录</a>`).join('');
}
async function applyCancelledRecord(id) {
  updateState(()=>{});
  if(!recordResult||page!=='records')return;
  const entry=recordResult.items.find(r=>r.id===id);if(entry)entry.status='cancelled';
  if(recordStatus&&recordStatus!=='cancelled') {
    recordResult={...recordResult,total:Math.max(0,recordResult.total-1),items:recordResult.items.filter(r=>r.id!==id)};
  }
  ++renderRevision;
  const html=await records({data:recordResult});
  replaceRegion('#record-rows',html);replaceRegion('.pagination',html);
  $('#record-total').textContent=recordResult.total+' 条记录';
}
let guardItems=[];
async function updateGuardList() {
  if(page!=='guards')return;
  try {const data=await api('/guards');if(page==='guards')replaceRegion('#guards-panel',await guards({data}));}
  catch(error){toast('名单已保存，列表刷新失败；请重新进入名单查看');}
}
async function removeGuardView(id) {
  guardItems=guardItems.filter(g=>g.id!==id);
  if(page==='guards')replaceRegion('#guards-panel',await guards({data:guardItems}));
}
async function applyCampaign(saved) {
  const index=broadcastItems.findIndex(t=>t.id===saved.id);
  if(index<0)broadcastItems.unshift(saved);else broadcastItems[index]=saved;
  if(page!=='broadcasts')return;
  const html=await broadcasts({data:broadcastItems});
  if(!replaceRegion(`[data-campaign-row="${saved.id}"]`,html))replaceRegion('#campaigns-panel',html);
}
