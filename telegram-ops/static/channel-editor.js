/* Channel curation has its own settings and review flow. Automatic mode applies only to newly collected posts and an explicitly configured target. */
let channelFeeds = [], channelFilter = 'all', channelFeedFilter = '', channelOffset = 0;
const channelStatuses = {draft:'待编辑',filtered:'已过滤',discarded:'已忽略',queued:'待发布',waiting:'平台等待',sending:'发布中',sent:'已发布',failed:'发布失败',unknown:'结果待核实'};
function channelSourceLink(d) {
  const feed=channelFeeds.find(f=>f.id===d.feed_id);
  const source=feed?.source&&!feed.source.startsWith('-')?feed.source:`c/${String(d.source_id).replace(/^-100/,'')}`;
  return `https://t.me/${source}/${d.message_ids[0]}`;
}
async function channels() {
  const [feeds, result]=await Promise.all([api('/channel-feeds'),api(`/channel-drafts?status=${channelFilter}&offset=${channelOffset}${channelFeedFilter?'&feed_id='+Number(channelFeedFilter):''}`)]);
  channelFeeds=feeds;
  return heading('频道采编','新帖自动筛选、统一排版，发布到你的频道。','<button id="new-channel-feed">＋ 新建采集</button>')+
    `<section class="panel"><div class="panel-head"><h2>采集来源</h2><span class="subtle">仅采集启用后的新消息</span></div>${feeds.length?feeds.map(f=>`<article class="task-row channel-feed"><div><div class="actions"><strong>${E(f.name)}</strong>${badge(f.enabled?'active':'disabled',f.enabled?'采集中':'已暂停')}</div><p>${E(f.source_title||f.source)} <span class="subtle">${f.source_id||''}</span></p><p class="subtle">${f.auto_publish?(f.target_id?'全自动发布':'自动排版 · 等待发布频道'):'人工确认发布'} · 发布到：${f.target_id?E(chatName(f.target_id))+' ('+f.target_id+')':'待配置你的曝光频道'} · 待编辑 ${f.counts.draft||0} · 已过滤 ${f.counts.filtered||0}</p><small>${f.checked_at?'上次核对：'+date(f.checked_at):'启用时验证频道读取权限'}${f.error?' · '+E(f.error):''}</small></div><div class="actions"><button class="secondary small" data-feed-edit="${f.id}">采集设置</button><button class="small ${f.enabled?'secondary':''}" data-feed-toggle="${f.id}">${f.enabled?'暂停采集':'验证并启用'}</button></div></article>`).join(''):empty('先连接一个来源频道','默认来源为 qt00。排除词“七天”和广告规则均可调整。','<button data-channel-new>配置来源频道</button>')}</section>`+
    `<section class="panel"><div class="panel-head channel-list-head"><h2>帖子工作区</h2><button class="secondary small" id="channel-refresh">刷新草稿</button></div><div class="channel-filters"><label>查看状态<select id="channel-status">${['draft','filtered','queued','sent','failed','unknown','discarded','all'].map(s=>`<option value="${s}" ${s===channelFilter?'selected':''}>${s==='all'?'全部状态':s==='queued'?'待发布 / 发布中':channelStatuses[s]}</option>`).join('')}</select></label><label>采集来源<select id="channel-feed-filter"><option value="">全部来源</option>${feeds.map(f=>`<option value="${f.id}" ${String(f.id)===channelFeedFilter?'selected':''}>${E(f.name)}</option>`).join('')}</select></label><span class="subtle">共 ${result.total} 条</span></div>${result.items.length?result.items.map(d=>`<article class="task-row channel-post"><div><div class="actions">${badge(d.status,channelStatuses[d.status])}<span class="subtle">${date(d.source_date)}</span></div><p class="channel-excerpt">${E(d.excerpt||'此帖只有附件')}</p><small>${E(feeds.find(f=>f.id===d.feed_id)?.name||'来源')} ${d.media.length?' · '+d.media.length+' 个附件':''}${d.reason?' · '+E(d.reason):''}</small></div><div class="actions"><a class="button secondary small" href="${E(channelSourceLink(d))}" target="_blank" rel="noopener noreferrer">查看原帖</a><button class="small secondary" data-draft-open="${d.id}">${['draft','failed'].includes(d.status)?'编辑草稿':'查看内容'}</button></div></article>`).join(''):empty(channelFilter==='filtered'?'暂无过滤记录':'这里还没有帖子',channelFilter==='draft'?'自动发布结果可在全部状态、待发布和已发布中查看；未设置目标的帖子保留在草稿。':'切换状态可查看其他帖子。')}<div class="channel-pager"><button class="secondary small" id="channel-prev" ${channelOffset===0?'disabled':''}>上一页</button><span>${Math.floor(channelOffset/30)+1} / ${Math.max(1,Math.ceil(result.total/30))}</span><button class="secondary small" id="channel-next" ${channelOffset+30>=result.total?'disabled':''}>下一页</button></div></section><p class="subtle">广告筛选依据帖子正文和图片说明中的规则词。图片或视频里的广告、隐晦推广仍需人工核对；附件发布时从原频道读取。</p>`;
}
function channelFeedDialog(feed=null) {
  const accounts=state.accounts.filter(a=>a.has_session);
  if(!accounts.length){toast('请先在账号管理登录一个 Telegram 账号');return;}
  const f=feed||{name:'曝光信息采集',source:'qt00',account_id:accounts.find(a=>a.role==='monitor')?.id||accounts[0].id,exclude_terms:'七天',filter_ads:true,ad_terms:'广告合作\n广告招商\n广告位招租\n付费推广\n商务合作\n推广合作',auto_publish:true,template_title:'曝光档案'};
  openModal(feed?'采集设置':'新建频道采集',`<form id="channel-feed-form">
    <label>采集名称<input name="name" required maxlength="120" value="${E(f.name)}"></label>
    <div class="form-grid"><label>采集和发布账号<select name="account_id">${accounts.map(a=>`<option value="${a.id}" ${a.id===f.account_id?'selected':''}>${E(a.name)}</option>`).join('')}</select><small>读取来源和向你的频道发布共用这个账号。</small></label><label>来源频道<input name="source" required value="${E(f.source)}" placeholder="https://t.me/qt00"><small>公开链接、@用户名或完整负数频道 ID。</small></label></div>
    <label>你的发布频道 ID（可稍后填）<input name="target_id" value="${f.target_id||''}" inputmode="numeric" placeholder="-100…"><small>账号需要该频道的发布消息权限。留空时仅采集排版，保留为草稿。</small></label>
    <div class="form-section"><h3>发布方式</h3><label>处理新帖<select name="auto_publish"><option value="true" ${f.auto_publish?'selected':''}>全自动排版并发布</option><option value="false" ${!f.auto_publish?'selected':''}>人工编辑确认发布</option></select></label>
    <div id="channel-template-settings"><label>模板标题<input name="template_title" maxlength="60" required value="${E(f.template_title||'曝光档案')}" placeholder="曝光档案"></label><div class="form-grid"><label>表情风格<select name="emoji_style">${channelOptions(channelStyleOptions,f.emoji_style||'alert')}</select></label><label>事件正文样式<select name="body_format">${channelOptions(channelBodyOptions,f.body_format||'normal')}</select></label></div><small>标题不带编号，底部不附原帖链接或附件说明。正文样式用于事件经过，账号信息保持清晰。</small><small>自动整理事件经过、涉及账号和原帖明确标注的交易信息，去除国籍及同步时间。未识别内容保留在正文，长文自动分段。</small></div></div>
    <div class="form-section"><h3>筛选规则</h3><label>出现这些词就整帖过滤<textarea name="exclude_terms" rows="3" placeholder="每行一个词">${E(f.exclude_terms)}</textarea><small>默认过滤“七天”。正文及相册图片说明中任一处命中，整帖进入已过滤记录。</small></label><label class="checkbox-label"><input type="checkbox" name="filter_ads" ${f.filter_ads?'checked':''}>过滤广告标记和推广用语</label><label>广告规则词<textarea name="ad_terms" rows="4">${E(f.ad_terms)}</textarea><small>每行一个词。规则只匹配帖子内容，不匹配来源频道名称。</small></label>
    <details><summary>用一段文字测试筛选与排版</summary><label>测试帖子<textarea name="sample" rows="4" placeholder="粘贴原帖正文，仅测试，不发布"></textarea></label><div class="actions"><button type="button" class="secondary small" id="channel-test-filter">测试筛选</button><button type="button" class="secondary small" id="channel-test-layout">预览自动排版</button></div><p id="channel-test-result" role="status"></p><div id="channel-layout-result" class="channel-preview" hidden></div></details></div>
    <p class="callout" id="channel-mode-description"></p><div class="form-error" role="alert"></div><div class="form-footer"><button type="submit">保存采集设置</button></div><small>保存后暂停运行，点击“验证并启用”开始处理后续新帖。旧草稿不会自动补发。</small></form>`);
  const updateMode=()=>{
    const automatic=$('#channel-feed-form [name=auto_publish]').value==='true';
    $('#channel-template-settings').hidden=false;
    $('#channel-mode-description').textContent=automatic?'筛选通过的新帖自动排版并发布，无需逐条确认。发布频道留空时只保存排版草稿。':'采集结果保存为草稿，由你编辑并确认后发布。';
  };
  $('#channel-feed-form [name=auto_publish]').onchange=updateMode;updateMode();
  $('#channel-test-filter').onclick=async e=>{
    const b=e.currentTarget;b.disabled=true;
    try{const fd=new FormData($('#channel-feed-form'));const r=await api('/channel-filter/preview','POST',{text:fd.get('sample'),exclude_terms:fd.get('exclude_terms'),ad_terms:fd.get('ad_terms'),filter_ads:fd.get('filter_ads')==='on'});$('#channel-test-result').textContent=r.reason;}
    catch(ex){$('#channel-test-result').textContent=ex.message;}finally{b.disabled=false;}
  };
  $('#channel-test-layout').onclick=async e=>{
    const b=e.currentTarget;b.disabled=true;
    try{const fd=new FormData($('#channel-feed-form'));const r=await api('/channel-layout/preview','POST',{text:fd.get('sample'),template_title:fd.get('template_title'),emoji_style:fd.get('emoji_style'),body_format:fd.get('body_format'),has_media:true});const box=$('#channel-layout-result');box.innerHTML='<div class="channel-preview-body">'+r.html+'</div><small>'+(r.split_body?'正文较长：附件后附完整正文，自动分段。':'发布排版预览，未发送消息。')+'</small>';channelPreparePreview(box);box.hidden=false;}
    catch(ex){$('#channel-test-result').textContent=ex.message;}finally{b.disabled=false;}
  };
  setupForm('#channel-feed-form',async fd=>{
    const target=String(fd.get('target_id')).trim();
    const payload={name:fd.get('name'),account_id:Number(fd.get('account_id')),source:fd.get('source'),target_id:target?parseGroupIds(target)[0]:null,exclude_terms:fd.get('exclude_terms'),ad_terms:fd.get('ad_terms'),filter_ads:fd.get('filter_ads')==='on',auto_publish:fd.get('auto_publish')==='true',template_title:fd.get('template_title'),emoji_style:fd.get('emoji_style'),body_format:fd.get('body_format')};
    await api('/channel-feeds'+(feed?'/'+feed.id:''),feed?'PUT':'POST',payload);$('#modal').close();await load();toast('设置已保存，点击“验证并启用”处理后续新帖');
  });
}

let channelDirty=false;
function channelCloseGuard(e) {
  if(!channelDirty)return;
  if(e.type==='click' && e.target!==$('#close-modal') && e.target!==$('#modal'))return;
  if(e.target===$('#modal') && e.type==='click'){
    const r=$('#modal').getBoundingClientRect();if(e.clientX>=r.left&&e.clientX<=r.right&&e.clientY>=r.top&&e.clientY<=r.bottom)return;
  }
  if(!confirm('有尚未保存的正文修改，确定关闭？')){e.preventDefault();e.stopImmediatePropagation();}
  else channelDirty=false;
}
document.addEventListener('DOMContentLoaded',()=>{
  $('#close-modal').addEventListener('click',channelCloseGuard,true);
  $('#modal').addEventListener('click',channelCloseGuard,true);
  $('#modal').addEventListener('cancel',channelCloseGuard);
  $('#modal').addEventListener('close',()=>{channelDirty=false;$('#modal').classList.remove('channel-dialog');});
});
window.addEventListener('beforeunload',e=>{if(channelDirty){e.preventDefault();e.returnValue='';}});
async function channelDraftDialog(id) {
  const d=await api(`/channel-drafts/${id}`), editable=['draft','failed'].includes(d.status), feed=channelFeeds.find(f=>f.id===d.feed_id);
  openModal('帖子草稿',`<div class="channel-draft-meta">${badge(d.status,channelStatuses[d.status])}<span>${date(d.source_date)}</span><a href="${E(channelSourceLink(d))}" target="_blank" rel="noopener noreferrer">打开原帖</a></div>${d.reason?`<p class="callout">${E(d.reason)}</p>`:''}<div class="channel-edit-layout"><section class="channel-original"><h3>原始内容</h3><div class="channel-original-text">${E(d.original_text||'此帖只有附件')}</div>${d.media.length?`<div class="channel-media">${d.media.map(m=>`<figure><img loading="lazy" src="/api/channel-drafts/${d.id}/thumbnail/${m.id}" alt="原帖${m.kind==='video'?'视频':'附件'}缩略图" onerror="this.hidden=true"><figcaption>${m.kind==='photo'?'图片':m.kind==='video'?'视频':'文件'} · ${m.id}</figcaption></figure>`).join('')}</div>`:''}</section><form id="channel-draft-form"><h3>${editable?'编辑发布内容':'已保存内容'}</h3>${editable?channelTextToolbar():''}<div id="channel-body" class="copy-editor channel-body" contenteditable="${editable}" role="textbox" aria-label="发布正文" aria-multiline="true"></div><div id="channel-link-fields" hidden><label>显示文字<input id="channel-link-text"></label><label>链接地址<input id="channel-link-url" placeholder="https://…"></label><button type="button" id="channel-link-insert" class="secondary small">插入链接</button></div>${d.media.length?`<label class="checkbox-label"><input id="channel-keep-media" type="checkbox" ${d.keep_media?'checked':''} ${editable?'':'disabled'}>保留原帖 ${d.media.length} 个附件（图片 / 视频 / 文件）</label>`:''}<small id="channel-length"></small><div class="channel-target">发布频道：<strong>${feed?.target_id?E(chatName(feed.target_id))+' ('+feed.target_id+')':'待配置'}</strong></div><div class="form-error" role="alert"></div><div class="form-footer">${editable?'<button type="submit">保存草稿</button><button type="button" id="channel-apply-layout" class="secondary">套用自动模板</button><button type="button" id="channel-preview" class="secondary">预览排版</button><button type="button" id="channel-publish">确认发布</button>':''}${['draft','filtered','failed'].includes(d.status)?'<button type="button" id="channel-discard" class="text-button">忽略此帖</button>':''}${['queued','waiting'].includes(d.status)?'<button type="button" id="channel-cancel" class="secondary">取消发布</button>':''}</div><p id="channel-save-feedback" role="status"></p><div id="channel-preview-box" class="channel-preview" hidden></div></form></div>`);
  $('#modal').classList.add('channel-dialog');channelDirty=false;
  const editor=$('#channel-body'),form=$('#channel-draft-form');
  editor.innerHTML=d.body_html;
  $('#channel-length').textContent='长文自动分段；带附件超过 1024 字符时另附完整正文。';
  let version=d.version,range=null;
  function mark(){channelDirty=true;const media=$('#channel-keep-media')?.checked;$('#channel-length').textContent=`${editor.innerText.length} 字符 · 长文自动分段；带附件超过 1024 字符时另附完整正文`;$('#channel-save-feedback').textContent='有未保存的修改';$('#channel-preview-box').hidden=true;}
  function remember(){const s=getSelection();if(s.rangeCount&&editor.contains(s.anchorNode)&&editor.contains(s.focusNode))range=s.getRangeAt(0).cloneRange();}
  function restore(){editor.focus();const s=getSelection();s.removeAllRanges();if(range&&editor.contains(range.commonAncestorContainer))s.addRange(range);else{const r=document.createRange();r.selectNodeContents(editor);r.collapse(false);s.addRange(r);}}
  editor.addEventListener('keyup',remember);editor.addEventListener('mouseup',remember);editor.addEventListener('input',()=>{remember();mark();});editor.addEventListener('click',e=>{if(e.target.closest('a'))e.preventDefault();});
  if(editable){
    editor.addEventListener('paste',e=>{e.preventDefault();document.execCommand('insertText',false,e.clipboardData.getData('text/plain'));mark();});editor.addEventListener('drop',e=>e.preventDefault());
    $$('[data-channel-format]').forEach(b=>{b.onmousedown=e=>e.preventDefault();b.onclick=()=>{restore();document.execCommand('styleWithCSS',false,false);document.execCommand(b.dataset.channelFormat,false);remember();mark();};});
    $$('[data-channel-wrap]').forEach(b=>{b.onmousedown=e=>e.preventDefault();b.onclick=()=>{
      restore();const next=channelReplaceSelection(editor,getSelection().getRangeAt(0),b.dataset.channelWrap);
      if(!next){$('#channel-draft-form .form-error').textContent='请先选中要设置格式的文字';return;}
      const selection=getSelection();selection.removeAllRanges();selection.addRange(next);remember();mark();
    };});
    function insertText(text){restore();document.execCommand('insertText',false,text);remember();mark();}
    $('#channel-emoji').onchange=e=>{if(e.target.value)insertText(e.target.value);e.target.value='';};
    $('#channel-date').onmousedown=e=>e.preventDefault();$('#channel-date').onclick=()=>{
      remember();$('#channel-date-fields').hidden=false;const d=new Date();d.setMinutes(d.getMinutes()-d.getTimezoneOffset());$('#channel-date-value').value=d.toISOString().slice(0,16);
    };
    $('#channel-date-insert').onclick=()=>{const value=$('#channel-date-value').value;if(value){insertText(value.replace('T',' '));$('#channel-date-fields').hidden=true;}};
    $('#channel-add-link').onmousedown=e=>e.preventDefault();$('#channel-add-link').onclick=()=>{remember();$('#channel-link-fields').hidden=false;$('#channel-link-text').value=range?.toString()||'';$('#channel-link-text').focus();};
    $('#channel-link-insert').onclick=()=>{try{const url=new URL($('#channel-link-url').value.trim()),text=$('#channel-link-text').value.trim();if(!['http:','https:'].includes(url.protocol)||url.username||url.password||!text)throw Error();restore();const s=getSelection(),r=s.getRangeAt(0),a=document.createElement('a');a.href=url.href;a.textContent=text;r.deleteContents();r.insertNode(a);r.setStartAfter(a);r.collapse(true);s.removeAllRanges();s.addRange(r);remember();mark();$('#channel-link-fields').hidden=true;}catch{$('#channel-draft-form .form-error').textContent='请填写显示文字和完整 http:// 或 https:// 链接';}};
    if($('#channel-keep-media'))$('#channel-keep-media').onchange=mark;
  }
  const value=()=>({version,body_html:channelEditorHtml(editor),keep_media:!!$('#channel-keep-media')?.checked});
  async function save(){const result=await api(`/channel-drafts/${id}`,'PUT',value());version=result.version;channelDirty=false;$('#channel-save-feedback').textContent='草稿已保存，尚未发布';return result;}
  setupForm('#channel-draft-form',async()=>{await save();toast('草稿已保存');});
  if($('#channel-apply-layout'))$('#channel-apply-layout').onclick=async e=>{
    if(!confirm('根据原帖重新生成排版并保存，替换当前编辑内容？'))return;
    const b=e.currentTarget;b.disabled=true;
    try{const r=await api(`/channel-drafts/${id}/format`,'POST',{version});version=r.version;editor.innerHTML=r.body_html;channelDirty=false;range=null;$('#channel-save-feedback').textContent='已套用模板并保存，尚未发布';$('#channel-preview-box').hidden=true;}
    catch(ex){$('#channel-draft-form .form-error').textContent=ex.message;}finally{b.disabled=false;}
  };
  if($('#channel-preview'))$('#channel-preview').onclick=()=>{const box=$('#channel-preview-box');box.innerHTML='<strong>发布排版预览</strong><div class="channel-preview-body">'+channelEditorHtml(editor)+'</div>';channelPreparePreview(box);box.hidden=false;};
  async function change(action){const result=await api(`/channel-drafts/${id}/${action}`,'POST',{version});version=result.version;channelDirty=false;$('#modal').close();await load();toast(action==='publish'?'已加入发布队列，请在“待发布 / 发布中”查看进度':action==='cancel'?'已取消发布':'已忽略此帖');}
  if($('#channel-publish'))$('#channel-publish').onclick=async e=>{const b=e.currentTarget;if(!feed?.target_id){$('#channel-draft-form .form-error').textContent='请先关闭草稿，在采集设置中填写你的曝光频道 ID';return;}if(!confirm(`将当前内容发布到频道 ${feed.target_id}？`))return;b.disabled=true;form.querySelector('button[type=submit]').disabled=true;try{await save();await change('publish');}catch(ex){$('#channel-draft-form .form-error').textContent=ex.message;}finally{if(b.isConnected){b.disabled=false;form.querySelector('button[type=submit]').disabled=false;}}};
  if($('#channel-discard'))$('#channel-discard').onclick=async e=>{if(channelDirty&&!confirm('放弃未保存的修改并忽略此帖？'))return;const b=e.currentTarget;b.disabled=true;try{await change('discard');}catch(ex){$('#channel-draft-form .form-error').textContent=ex.message;b.disabled=false;}};
  if($('#channel-cancel'))$('#channel-cancel').onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{await change('cancel');}catch(ex){$('#channel-draft-form .form-error').textContent=ex.message;b.disabled=false;}};
}
function bindChannels() {
  if(page!=='channels')return;
  $('#new-channel-feed').onclick=()=>channelFeedDialog();$$('[data-channel-new]').forEach(b=>b.onclick=()=>channelFeedDialog());
  $$('[data-feed-edit]').forEach(b=>b.onclick=()=>channelFeedDialog(channelFeeds.find(f=>f.id===Number(b.dataset.feedEdit))));
  $$('[data-feed-toggle]').forEach(b=>b.onclick=()=>{const f=channelFeeds.find(f=>f.id===Number(b.dataset.feedToggle));action(b,()=>api(`/channel-feeds/${f.id}/${f.enabled?'pause':'start'}`,'POST'),f.enabled?'采集已暂停':'采集已启用，从现在起收集新帖');});
  $('#channel-refresh').onclick=e=>action(e.currentTarget,async()=>{},'草稿已刷新');
  for(const id of ['channel-status','channel-feed-filter'])$('#'+id).onchange=async()=>{channelFilter=$('#channel-status').value;channelFeedFilter=$('#channel-feed-filter').value;channelOffset=0;try{await load();}catch(ex){toast(ex.message);}};
  $('#channel-prev').onclick=async()=>{channelOffset=Math.max(0,channelOffset-30);try{await load();}catch(ex){toast(ex.message);}};
  $('#channel-next').onclick=async()=>{channelOffset+=30;try{await load();}catch(ex){toast(ex.message);}};
  $$('[data-draft-open]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await channelDraftDialog(Number(b.dataset.draftOpen));}catch(ex){toast(ex.message);}finally{b.disabled=false;}});
}
