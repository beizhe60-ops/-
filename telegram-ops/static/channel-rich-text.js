/* Channel editor formats are isolated from the private-message editor. */
const channelStyleOptions={alert:'⚠️ 警示风格',lively:'🚨 醒目风格',minimal:'简洁 · 无表情'};
const channelBodyOptions={normal:'普通正文',bold:'加粗',italic:'斜体',underline:'下划线',strike:'删除线',quote:'引用',code:'等宽',spoiler:'剧透'};
function channelOptions(options,value){return Object.entries(options).map(([id,label])=>`<option value="${id}" ${id===value?'selected':''}>${label}</option>`).join('');}
function channelEditorHtml(node){
  let out='';
  for(const child of node.childNodes){
    if(child.nodeType===Node.TEXT_NODE){out+=E(child.textContent);continue;}
    if(child.nodeType!==Node.ELEMENT_NODE)continue;
    const name=child.tagName.toLowerCase(),tag={strong:'b',em:'i',s:'del',strike:'del'}[name]||name;
    if(tag==='br'){out+='\n';continue;}
    const content=tag==='code'?E(child.textContent):channelEditorHtml(child);
    if(['div','p'].includes(tag)){if(out&&!out.endsWith('\n'))out+='\n';out+=content;continue;}
    if(['b','i','u','del','code','blockquote','tg-spoiler'].includes(tag))out+=`<${tag}>${content}</${tag}>`;
    else if(tag==='a'){
      try{const url=new URL(child.getAttribute('href'));if(!['https:','http:'].includes(url.protocol)||url.username||url.password)throw Error();out+=`<a href="${E(url.href)}">${content}</a>`;}catch{out+=content;}
    }else out+=content;
  }
  return out;
}
function channelTextToolbar(){return `<div class="copy-toolbar channel-toolbar" role="toolbar" aria-label="正文格式">
  ${Object.entries({bold:'加粗',italic:'斜体',underline:'下划线',strikeThrough:'删除线'}).map(([cmd,label])=>`<button type="button" class="secondary small" data-channel-format="${cmd}">${label}</button>`).join('')}
  ${Object.entries({blockquote:'引用',code:'等宽','tg-spoiler':'剧透',plain:'纯文本'}).map(([tag,label])=>`<button type="button" class="secondary small" data-channel-wrap="${tag}">${label}</button>`).join('')}
  <button type="button" class="secondary small" id="channel-add-link">文字链接</button><button type="button" class="secondary small" data-channel-format="unlink">移除链接</button>
  <button type="button" class="secondary small" id="channel-date">插入日期</button>
  <label class="channel-emoji-label">表情<select id="channel-emoji" aria-label="插入表情"><option value="">选择表情</option>${['⚠️','🚨','📝','🔎','👤','🆔','💰','🧾','📌','📅','✅','❗','📎','🔗'].map(e=>`<option value="${e}">${e}</option>`).join('')}</select></label>
</div><div id="channel-date-fields" class="channel-inline-fields" hidden><label>日期与时间<input id="channel-date-value" type="datetime-local"></label><button type="button" id="channel-date-insert" class="secondary small">插入日期文字</button><small>以当前填写的日期和时间显示。</small></div>`;}
// Lift just the selected content out of surrounding styles, retaining both unselected sides.
function channelReplaceSelection(editor,range,tag){
  if(range.collapsed)return null;
  const content=range.extractContents(),box=document.createElement('div');box.append(content);
  const marker=document.createElement('span');range.insertNode(marker);
  while(marker.parentNode!==editor){
    const parent=marker.parentNode;
    const before=document.createRange();before.selectNodeContents(parent);before.setEndBefore(marker);
    const after=document.createRange();after.selectNodeContents(parent);after.setStartAfter(marker);
    const left=parent.cloneNode(false),right=parent.cloneNode(false);left.append(before.cloneContents());right.append(after.cloneContents());
    parent.replaceWith(...(left.hasChildNodes()?[left]:[]),marker,...(right.hasChildNodes()?[right]:[]));
  }
  box.innerHTML=channelEditorHtml(box);
  const text=box.textContent;
  const replacement=tag==='plain'?document.createTextNode(text):document.createElement(tag);
  if(tag!=='plain')replacement.textContent=text;
  marker.replaceWith(replacement);
  const selected=document.createRange();selected.selectNodeContents(replacement);return selected;
}

function channelPreparePreview(box){
  box.querySelectorAll('a').forEach(a=>{a.target='_blank';a.rel='noopener noreferrer';});
  box.querySelectorAll('tg-spoiler').forEach(el=>{
    el.tabIndex=0;el.setAttribute('role','button');el.setAttribute('aria-label','显示或隐藏剧透内容');el.setAttribute('aria-pressed','false');
    const toggle=()=>el.setAttribute('aria-pressed',String(el.classList.toggle('revealed')));
    el.onclick=toggle;el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();toggle();}};
  });
}
