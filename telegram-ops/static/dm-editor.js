/* Private-message editor: restricted formatting, local uploads and no-send preview. */
function copyEditorMarkup(a) {
  return `<section class="copy-composer"><label id="copy-editor-label">私信文案</label><div class="copy-toolbar" role="toolbar" aria-label="文字格式"><button type="button" class="secondary small" data-copy-command="bold"><b>加粗</b></button><button type="button" class="secondary small" data-copy-command="italic"><i>斜体</i></button><button type="button" class="secondary small" data-copy-command="underline">下划线</button><button type="button" class="secondary small" id="copy-link">文字链接</button><button type="button" class="secondary small" data-copy-command="unlink">移除链接</button><button type="button" class="secondary small" data-copy-insert="{{ username }}">插入用户名</button></div><div id="copy-editor" class="copy-editor" contenteditable="true" role="textbox" aria-multiline="true" aria-labelledby="copy-editor-label"></div><div class="copy-toolbar" aria-label="常用表情">${['😊','🎉','✅','📌','👇','✨','📱','🎁'].map(e=>`<button type="button" class="secondary small" data-copy-insert="${e}">${e}</button>`).join('')}</div><small>选中文字后设置格式。支持直接输入表情，{{ username }} 会替换为收件人用户名。</small><div id="copy-link-fields" class="copy-link-fields" hidden><label>显示文字<input id="copy-link-label" placeholder="例如：查看详情"></label><label>链接地址<input id="copy-link-url" type="url" placeholder="https://example.com"></label><button type="button" id="copy-insert-link" class="secondary small">插入链接</button></div><div class="copy-media"><label>配图（可选）<input id="copy-image-file" type="file" accept="image/jpeg,image/png,image/webp"></label><small>单张静态 JPG、PNG 或 WebP，最大 5 MB。带图片的文案上限为 1024 字符，纯文字为 3500 字符；表情可能占两个字符。</small><img id="copy-image-preview" alt="当前私信配图" hidden><button type="button" id="copy-remove-image" class="secondary small" hidden>移除配图</button><p id="copy-upload-status" role="status"></p></div><button type="button" id="copy-preview" class="secondary">预览图文（不发送）</button><div id="copy-preview-panel" class="copy-preview-panel" hidden></div><p id="copy-feedback" role="status"></p></section>`;
}

function editorHtml(node) {
  // Serialize browser editing markup into the server's restricted Telegram subset.
  let output='';
  for(const child of node.childNodes){
    if(child.nodeType===Node.TEXT_NODE){output+=E(child.textContent);continue;}
    if(child.nodeType!==Node.ELEMENT_NODE)continue;
    const name=child.tagName.toLowerCase(), content=editorHtml(child);
    if(name==='br'){output+='\n';continue;}
    if(['div','p'].includes(name)){if(output && !output.endsWith('\n'))output+='\n';output+=content;continue;}
    const tag={strong:'b',em:'i',s:'del',strike:'del'}[name]||name;
    if(['b','i','u','del','code'].includes(tag))output+=`<${tag}>${content}</${tag}>`;
    else if(tag==='a'){
      try{const url=new URL(child.getAttribute('href'));if(!['https:','http:'].includes(url.protocol)||url.username||url.password)throw Error();output+=`<a href="${E(url.href)}">${content}</a>`;}catch{output+=content;}
    }else output+=content;
  }
  return output;
}

function bindCopyEditor(a) {
  const editor=$('#copy-editor'),form=$('#receive-groups-form');
  let imageId=a.dm_copy_options?.image_id||null, range=null, uploading=false;
  if(a.dm_copy_options?.format==='html'){
    const doc=new DOMParser().parseFromString(a.dm_template||'', 'text/html');
    editor.innerHTML=editorHtml(doc.body);
  }else editor.textContent=a.dm_template||'';
  function remember(){const s=getSelection();if(s.rangeCount&&editor.contains(s.anchorNode)&&editor.contains(s.focusNode))range=s.getRangeAt(0).cloneRange();}
  function restore(){editor.focus();const s=getSelection();s.removeAllRanges();if(range&&editor.contains(range.commonAncestorContainer))s.addRange(range);else{const r=document.createRange();r.selectNodeContents(editor);r.collapse(false);s.addRange(r);}}
  function selectionChanged(){if(!editor.isConnected){document.removeEventListener('selectionchange',selectionChanged);return;}remember();}
  document.addEventListener('selectionchange',selectionChanged);
  function hidePreview(){$('#copy-preview-panel').hidden=true;}
  editor.addEventListener('keyup',remember);editor.addEventListener('mouseup',remember);editor.addEventListener('input',()=>{remember();hidePreview();});
  editor.addEventListener('click',e=>{if(e.target.closest('a'))e.preventDefault();});
  editor.addEventListener('paste',e=>{e.preventDefault();document.execCommand('insertText',false,e.clipboardData.getData('text/plain'));remember();hidePreview();});
  editor.addEventListener('drop',e=>e.preventDefault());
  $$('[data-copy-command]').forEach(b=>{b.onmousedown=e=>e.preventDefault();b.onclick=()=>{restore();document.execCommand('styleWithCSS',false,false);document.execCommand(b.dataset.copyCommand,false);remember();hidePreview();};});
  $$('[data-copy-insert]').forEach(b=>{b.onmousedown=e=>e.preventDefault();b.onclick=()=>{restore();document.execCommand('insertText',false,b.dataset.copyInsert);remember();hidePreview();};});
  $('#copy-link').onmousedown=e=>e.preventDefault();
  $('#copy-link').onclick=()=>{remember();$('#copy-link-fields').hidden=false;$('#copy-link-label').value=range?.toString()||'';$('#copy-link-label').focus();};
  $('#copy-insert-link').onclick=()=>{
    try{const url=new URL($('#copy-link-url').value.trim());if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw Error();const label=$('#copy-link-label').value.trim();if(!label)throw Error();restore();const s=getSelection(),r=s.getRangeAt(0);const link=document.createElement('a');link.href=url.href;link.textContent=label;r.deleteContents();r.insertNode(link);r.setStartAfter(link);r.collapse(true);s.removeAllRanges();s.addRange(r);remember();$('#copy-link-fields').hidden=true;$('#copy-feedback').textContent='';hidePreview();}
    catch{$('#copy-feedback').textContent='请填写显示文字和完整 http:// 或 https:// 链接';}
  };
  function paintImage(){const img=$('#copy-image-preview');img.hidden=!imageId;$('#copy-remove-image').hidden=!imageId;if(imageId)img.src='/api/message-assets/'+imageId;else img.removeAttribute('src');}
  paintImage();
  $('#copy-remove-image').onclick=()=>{imageId=null;paintImage();hidePreview();};
  $('#copy-image-file').onchange=async e=>{
    const file=e.target.files[0];if(!file)return;
    if(file.size>5*1024*1024){$('#copy-upload-status').textContent='请选择 5 MB 以内的图片';return;}
    uploading=true;$('#copy-upload-status').textContent='正在上传图片…';form.querySelector('button[type=submit]').disabled=true;
    try{const fd=new FormData();fd.append('file',file);const r=await fetch('/api/message-assets',{method:'POST',headers:{'X-Console-Request':'1'},body:fd});const data=await r.json();if(!form.isConnected)return;if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'图片上传失败');imageId=data.id;paintImage();hidePreview();$('#copy-upload-status').textContent='图片已上传，保存绑定后生效';}
    catch(ex){if(form.isConnected)$('#copy-upload-status').textContent=ex.message;}finally{uploading=false;if(form.isConnected)form.querySelector('button[type=submit]').disabled=false;e.target.value='';}
  };
  function value(){if(uploading)throw Error('请等待图片上传完成');return {template:editorHtml(editor).trim(),format:'html',image_id:imageId};}
  $('#copy-preview').onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{const v=value();const r=await api('/message-copy/preview','POST',v);if(!form.isConnected)return;const panel=$('#copy-preview-panel');panel.innerHTML=(r.image_url?`<img src="${E(r.image_url)}" alt="配图预览">`:'')+`<div class="copy-preview-text">${r.html}</div>`;panel.querySelectorAll('a').forEach(a=>{a.target='_blank';a.rel='noopener noreferrer';});panel.hidden=false;$('#copy-feedback').textContent='仅预览，没有发送消息；实际外观以 Telegram 为准。';}catch(ex){if(form.isConnected)$('#copy-feedback').textContent=ex.message;}finally{b.disabled=false;}};
  return value;
}
