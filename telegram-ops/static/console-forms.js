/* Unsaved input lives in memory only. Never store drafts or credentials in browser storage. */
const watchedForms = new Map();
const guardedFormIds = new Set(['ai-form','task-form','receive-groups-form','channel-feed-form','broadcast-form','weight-form','guard-form']);
function formSnapshot(form, extra) {
  const fields=[...form.querySelectorAll('input[name],textarea[name],select[name]')]
    .filter(e=>!e.name.startsWith('pick-') && e.name!=='sample' && e.type!=='file')
    .map(e=>[e.name, ['checkbox','radio'].includes(e.type)?e.checked:e.value]);
  return {fields,extra:extra?extra():null};
}
function watchForm(form, extra) {
  if(watchedForms.has(form) && !extra)return;
  watchedForms.set(form,{extra,baseline:formSnapshot(form,extra)});
}
function dirtyForms(scope=document) {
  const dirty=[];
  for(const [form,record] of watchedForms) {
    if(!form.isConnected){watchedForms.delete(form);continue;}
    if(!scope.contains(form)||form.closest('dialog')?.open===false)continue;
    try {if(JSON.stringify(formSnapshot(form,record.extra))!==JSON.stringify(record.baseline))dirty.push(form);}
    catch {dirty.push(form);}
  }
  return dirty;
}
function confirmLeave(scope=document) {
  const modal=$('#modal');
  if(!dirtyForms(scope).length && !(modal.open && scope.contains(modal) && channelDirty))return true;
  return confirm('有尚未保存的修改。离开将放弃这些修改，确定继续？');
}
function savedForm(form, submitted) {
  const record=watchedForms.get(form);if(!record||!submitted)return;
  // API keys are cleared after an acknowledged save. Later edits remain dirty.
  submitted.fields=submitted.fields.map(([name,value])=>[name,name==='api_key'?'':value]);
  record.baseline=submitted;
}
function foldSection(node, title, open=false) {
  const details=document.createElement('details');details.className='form-step';details.open=open;
  const summary=document.createElement('summary');summary.textContent=title;
  node.before(details);details.append(summary,node);return details;
}
function organizeForm(form) {
  if(form.dataset.organized)return;form.dataset.organized='true';
  if(form.id==='task-form') {
    const grid=form.querySelector(':scope > .form-grid');
    const target=grid?.querySelector('[data-picker="relay_chat"]')?.parentElement;
    if(target) {
      const destination=document.createElement('div');destination.className='form-section';
      form.querySelector(':scope > .form-section').after(destination);
      destination.append(target);
      foldSection(destination,'3. 转发目标',!target.querySelector('[name=relay_chat]').value);
    }
  }
  const groups={
    'task-form':[['1. 来源账号与群组',true],['2. 关键词与过滤规则',false]],
    'ai-form':[['1. 分析来源',true],['2. 摘要规则与时间',false],['3. 推送目标',false]],
    'channel-feed-form':[['发布方式与排版',false],['过滤规则与测试',false]],
    'broadcast-form':[['1. 通知内容',true],['2. 接收群组',true],['3. 发送时间',false]],
  };
  if(groups[form.id]) {
    const selector=form.id==='task-form'?':scope > .form-grid, :scope > .form-section':':scope > .form-section';
    [...form.querySelectorAll(selector)].forEach((node,i)=>{
      const config=groups[form.id][i];if(!config)return;
      node.querySelector(':scope > h3, :scope > .section-title')?.remove();
      foldSection(node,...config);
    });
  }
  if(form.id==='channel-feed-form') {
    const grid=form.querySelector(':scope > .form-grid');if(grid)foldSection(grid,'来源账号与频道',true);
  }
  if(form.id==='receive-groups-form') {
    const copy=form.querySelector('.copy-composer');if(copy)foldSection(copy,'私信文案、格式与配图',true);
  }
  // Native validation must reveal a required field before the browser focuses it.
  form.addEventListener('invalid',e=>revealField(e.target),true);
  form.querySelectorAll('[required]').forEach(input=>input.closest('label')?.classList.add('required-field'));
  if(form.querySelector('.form-step')) {
    const hint=document.createElement('p');hint.className='page-note';hint.textContent='带青色左边框的输入项为必填。展开各分区调整设置，最后保存。';form.prepend(hint);
    const control=document.createElement('button');control.type='button';control.className='text-button small form-expand';control.textContent='展开所有设置';
    control.onclick=()=>form.querySelectorAll('.form-step').forEach(d=>d.open=true);form.prepend(control);
  }
}
function revealField(field) {
  for(let node=field?.parentElement;node;node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;
}
document.addEventListener('DOMContentLoaded',()=>{
  const modal=$('#modal');
  const guard=e=>{
    if(e.type==='click'&&e.currentTarget===modal){
      if(e.target!==modal)return;
      const r=modal.getBoundingClientRect();if(e.clientX>=r.left&&e.clientX<=r.right&&e.clientY>=r.top&&e.clientY<=r.bottom)return;
    }
    if(writeRequests || modal.querySelector('form[inert]')) {e.preventDefault();e.stopImmediatePropagation();toast('操作正在处理，请稍候再关闭');return;}
    if(!confirmLeave(modal)){e.preventDefault();e.stopImmediatePropagation();}
  };
  $('#close-modal').addEventListener('click',guard,true);
  modal.addEventListener('click',guard,true);modal.addEventListener('cancel',guard,true);
  modal.addEventListener('close',()=>{channelDirty=false;modal.classList.remove('channel-dialog');});
  document.addEventListener('click',e=>{
    const link=e.target.closest('a[href="/admin/logout"]');
    if(link&&!confirmLeave()){e.preventDefault();e.stopImmediatePropagation();}
  },true);
});
window.addEventListener('beforeunload',e=>{
  if(dirtyForms().length || ($('#modal')?.open&&channelDirty)){e.preventDefault();e.returnValue='';}
});
