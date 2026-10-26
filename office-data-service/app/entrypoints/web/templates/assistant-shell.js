/* 会话ID与展开偏好按账号在本标签页保存；正文、回执和审批不写浏览器持久存储。 */
(() => {
  const api=window.OfficeAssistant;
  if (!api) return;
  const {root,view}=api, full=root.dataset.full==='true';
  const $=selector=>root.querySelector(selector), launch=document.getElementById('assistant-launch');
  const list=$('#assistant-conversations'), messages=$('#assistant-messages'), input=$('#assistant-input'), send=$('[data-assistant-send]'), feedback=$('#assistant-feedback');
  const archived=$('#assistant-archived'), archive=$('[data-assistant-archive]'), more=$('[data-assistant-more]'), earlier=$('[data-assistant-earlier]');
  const storeKey='office-assistant:'+root.dataset.owner;
  const drafts=new Map(), rendered=new Map();
  let followLatest=true, width=420;
  const rename=$('[data-assistant-rename]'), query=$('#assistant-query'), resize=$('.assistant-resize');
  function setWidth(value){const maximum=Math.max(360,Math.min(640,innerWidth-800));width=Math.min(maximum,Math.max(360,value));resize.setAttribute('aria-valuemax',String(maximum));document.documentElement.style.setProperty('--assistant-width',width+'px');resize.setAttribute('aria-valuenow',String(width));}
  const latest=$('[data-assistant-latest]');
  function sizeInput(){input.style.height='auto';input.style.height=Math.min(180,Math.max(76,input.scrollHeight))+'px';}
  function rememberDraft(){drafts.set(state.id,input.value);}
  function restoreDraft(){input.value=drafts.get(state.id)||'';sizeInput();}
  function jumpLatest(){followLatest=true;messages.scrollTop=messages.scrollHeight;latest.hidden=true;}
  const pages={dashboard:'仪表盘',employees:'人员管理',roles:'角色', 'permission-sets':'权限集', permissions:'权限目录',audit:'审计','client-authorizations':'客户端授权',assistant:'智能助手'};
  const path=location.pathname.split('/')[2], page=Object.hasOwn(pages,path) ? path : '';
  $('#assistant-page-label').textContent=pages[page] || '未关联';
  const state={id:'',conversation:null,turns:[],next:'',listNext:'',ready:false,loading:false,busy:false,failures:0,polls:0,timer:null,blocked:false,version:0,epoch:0};
  try {state.id=JSON.parse(sessionStorage.getItem(storeKey)||'{}').id||'';} catch (_) { /* 存储不可用时仍可使用。 */ }
  function save() {try {sessionStorage.setItem(storeKey,JSON.stringify({id:state.id,open:full||!root.hidden,width}));} catch (_) {}}
  function say(text,error=false) {if(feedback.textContent!==text)feedback.textContent=text; feedback.classList.toggle('is-error',error);}
  function clearPoll() {clearTimeout(state.timer);state.timer=null;}
  function showError(error) {
    state.failures++;
    say(error.message+(error.trace ? ' 追踪号：'+error.trace : ''),true);
    if ([401,403].includes(error.status)) {
      state.blocked=true; state.turns=[];state.conversation=null;list.replaceChildren(new Option('当前身份或权限不可用','')); messages.replaceChildren(view.el('p','当前身份或权限不可用，请重新登录或联系管理员。','hint'));
      drafts.clear();input.value='';rendered.clear();clearPoll();
    }
  }
  function updateControls() {
    const working=state.turns.some(turn=>view.active.has(turn.status));
    const reason=state.blocked?'当前身份或权限不可用':!state.ready?'助手尚未就绪':state.busy?'正在提交操作':state.loading?'正在同步会话':state.conversation?.archived?'请先恢复归档会话':working?'本轮任务仍在进行，可以先起草下一条':!input.value.trim()?'请输入工作要求':'';
    send.disabled=!!reason;send.title=reason||'发送工作要求';$('#assistant-input-hint').textContent=reason||'Enter 换行 · Ctrl / ⌘ + Enter 发送';
    input.disabled=state.blocked;
    rename.disabled=state.busy||state.loading||!state.conversation||state.blocked;
    archive.disabled=state.busy||state.loading||!state.conversation||working||state.blocked;
    archive.textContent=state.conversation?.archived ? '恢复' : '归档';
    list.disabled=state.busy||state.loading; $('[data-assistant-new]').disabled=state.busy||state.loading||state.blocked;
  }
  function paint() {
    const nearBottom=messages.scrollHeight-messages.scrollTop-messages.clientHeight<90;
    const openPreview=!!messages.querySelector('details[open]');
    const selection=getSelection();
    const selecting=selection&&!selection.isCollapsed&&messages.contains(selection.anchorNode);
    const anchor=Array.from(messages.children).find(node=>node.getBoundingClientRect().bottom>messages.getBoundingClientRect().top);
    const anchorTop=anchor?.getBoundingClientRect().top;
    const ids=new Set(state.turns.map(turn=>turn.id));
    messages.querySelectorAll('[data-placeholder]').forEach(node=>node.remove());
    for(const [id,entry] of rendered){if(!ids.has(id)){entry.node.remove();rendered.delete(id);}}
    let changed=false;
    state.turns.forEach(turn=>{
      const signature=JSON.stringify(turn), entry=rendered.get(turn.id);
      if(entry?.signature===signature)return;
      // 未改变的消息保留原节点、文本选择与焦点；只有这一轮状态变化才更新。
      const node=view.renderTurn(turn,controls);
      if(entry){
        const expanded=Array.from(entry.node.querySelectorAll('details')).map(item=>item.open);
        const focused=entry.node.contains(document.activeElement)?document.activeElement.textContent:null;
        entry.node.replaceWith(node);
        node.querySelectorAll('details').forEach((item,index)=>{item.open=!!expanded[index];});
        if(focused)Array.from(node.querySelectorAll('button')).find(item=>item.textContent===focused)?.focus({preventScroll:true});
      }
      rendered.set(turn.id,{node,signature});changed=true;
    });
    // 按已有顺序插入，避免移动未改变的节点导致选择失效。
    let cursor=messages.firstElementChild;
    state.turns.forEach(turn=>{const node=rendered.get(turn.id).node;if(node!==cursor)messages.insertBefore(node,cursor);cursor=node.nextElementSibling;});
    if(!state.turns.length){const empty=view.el('p','开始一个工作要求，或选择其他会话继续。','empty');empty.dataset.placeholder='true';messages.append(empty);}
    earlier.hidden=!state.next||state.turns.length>=50;
    if(state.turns.length>=50&&state.next){const note=view.el('p','当前界面最多展示50轮；历史仍保存在服务器，可通过有界接口继续查询。','small muted');note.dataset.placeholder='true';messages.prepend(note);}
    if(followLatest&&nearBottom&&!openPreview&&!selecting)jumpLatest();
    else {if(anchor?.isConnected)messages.scrollTop+=anchor.getBoundingClientRect().top-anchorTop;if(changed)latest.hidden=false;}
    updateControls();
  }
  function schedule() {
    clearPoll();
    if (root.hidden||document.hidden||state.blocked||!state.id||state.polls>=120||state.failures>=4) return;
    if (!state.turns.some(turn=>view.active.has(turn.status))) return;
    state.timer=setTimeout(()=>loadConversation(),Math.min(15000,2000*2**state.failures));
  }
  async function loadConversation(before='') {
    if (state.loading||state.busy||!state.id||state.blocked) return;
    if(document.querySelector('dialog[open]')){schedule();return;}
    state.loading=true;updateControls(); const id=state.id;
    try {
      const item=await api.get('conversations/'+encodeURIComponent(id)+(before?'?before='+encodeURIComponent(before):''));
      if (id!==state.id) return;
      const changed=state.epoch!==item.access_fingerprint || state.version!==item.version || JSON.stringify(state.turns.slice(-10))!==JSON.stringify(item.turns);
      if (state.epoch && state.epoch!==item.access_fingerprint) state.turns=[];
      if (before) {const height=messages.scrollHeight, offset=messages.scrollTop; state.turns=[...item.turns,...state.turns].slice(-50);state.next=item.next;paint();messages.scrollTop=offset+messages.scrollHeight-height;}
      else {
        const previous=new Map(state.turns.map(turn=>[turn.id,turn])); item.turns.forEach(turn=>previous.set(turn.id,turn));
        state.turns=Array.from(previous.values()).sort((a,b)=>a.created_at.localeCompare(b.created_at)||a.id.localeCompare(b.id)).slice(-50);
        if (!state.conversation || state.turns.length<=item.turns.length) state.next=item.next;
      }
      if(!Array.from(list.options).some(option=>option.value===item.id))list.append(new Option(item.title+(item.archived?'（已归档）':''),item.id));
      list.value=item.id;
      state.conversation=item;state.epoch=item.access_fingerprint;state.version=item.version; $('#assistant-session-info').textContent='更新于 '+new Date(item.updated_at).toLocaleString('zh-CN')+' · '+(item.archived?'已归档':'本人会话'); state.failures=0;state.polls++;
      if (changed && !before) paint(); else updateControls();
      if(state.actionError)say(state.actionError,true);
      else if (item.history_reset) say('权限配置已变化，旧内容已隐藏；新问题仅使用当前可用信息。');
      else if (item.archived) say('此会话已归档；恢复后可继续。');
      else if (!state.turns.some(turn=>view.active.has(turn.status))) say('已同步。模型说明仅作辅助，请核对实际回执。');
      else if(state.turns.some(turn=>turn.pending&&!('decision' in turn.pending)))say('有操作等待您核对，批准后才会继续。');
      else say('任务正在处理；需要确认时会在这里显示。');
      if (state.polls>=120) say('已暂停自动更新。点击“重新加载”继续核对任务。');
    } catch(error) {showError(error);} finally {state.loading=false;updateControls();schedule();}
  }
  async function bootstrap(append=false) {
    if(state.busy||state.loading||state.blocked) return;
    state.loading=true;updateControls();
    try {
      const data=await api.get('bootstrap?archived='+archived.checked+'&q='+encodeURIComponent(query.value.trim())+(append?'&after='+encodeURIComponent(state.listNext):''));
      state.ready=data.capability.ready;
      if (!append) list.replaceChildren(new Option('选择会话',''));
      data.conversations.items.forEach(item=>{const option=new Option(item.title,item.id);list.append(option);});
      state.listNext=data.conversations.next||'';more.hidden=!state.listNext;list.value=state.id;
      say(state.ready ? '选择会话或直接输入工作要求。' : (data.capability.message||'智能助手尚未启用，请联系管理员配置。'));
    } catch(error) {showError(error);} finally {state.loading=false;updateControls();}
    if(state.id&&!append&&!query.value.trim()) await loadConversation();
  }
  function resetConversation(id='') {rememberDraft();clearPoll();rendered.clear();messages.replaceChildren();followLatest=true;state.id=id;restoreDraft();state.conversation=null;state.turns=[];state.next='';state.version=0;state.epoch=0;state.polls=0;state.failures=0;state.intent=null;state.actionError='';$('#assistant-session-info').textContent='';save();paint();}
  async function withAction(fn) {
    if(state.busy||state.blocked)return;
    if(state.loading){say('正在同步状态，请稍后再操作。');return;}
    clearPoll();state.busy=true;updateControls();
    try {await fn();state.failures=0;state.actionError='';} catch(error) {state.actionError=error.message;showError(error);} finally {state.busy=false;updateControls();}
    if(state.id) await loadConversation();
  }
  const controls={pageName:code=>pages[code]||'未关联页面',
    async refreshBusiness() {
      const main=document.querySelector('main');
      if(window.OfficeForms?.dirty(main)&&!await window.OfficeUI.confirm('当前页面有未保存的修改。刷新会丢失这些内容，是否继续？',{title:'刷新业务页面？',accept:'放弃修改并刷新'}))return;
      location.reload();
    },
    async decide(turn,approve) {
      if(!await window.OfficeUI.confirm(approve?'请核对本次完整预览。批准仅适用于此操作，后续写入仍需确认。':'拒绝本次操作？助手不会执行该提案。',{title:approve?'批准本次操作？':'拒绝操作',accept:approve?'批准本次操作':'拒绝操作'})) return;
      await withAction(async()=>{await api.action('decide',{identifier:turn.id,version:turn.version,proposal_id:turn.pending.ticket,approve});say('决定已记录，等待实际执行回执。');});
    },
    async stop(turn) {
      if(!await window.OfficeUI.confirm('已完成的操作不会撤回，在途写入可能需要核对实际数据。',{title:'停止任务？',accept:'停止任务'})) return;
      await withAction(()=>api.action('cancel',{identifier:turn.id,version:turn.version}));
    }
  };
  $('#assistant-composer').addEventListener('submit',async event=>{
    event.preventDefault(); if(send.disabled||!input.value.trim())return;
    const prompt=input.value.trim();
    if(state.intent?.prompt!==prompt||state.intent?.id!==state.id)state.intent={prompt,id:state.id,key:Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join(''),page:$('#assistant-context').checked?page:''};
    const request_key=state.intent.key;
    await withAction(async()=>{
      if(!state.id) {const item=await api.action('create',{title:prompt.slice(0,40)});const intent=state.intent, draft=input.value;resetConversation(item.id);input.value=draft;rememberDraft();sizeInput();state.intent=intent;list.append(new Option(item.title,item.id));list.value=item.id;state.intent.id=item.id;}
      await api.action('start',{prompt,request_key,conversation_id:state.id,context_page:state.intent.page});
      if(input.value.trim()===prompt){input.value='';drafts.delete(state.id);}else rememberDraft();drafts.delete('');sizeInput();state.intent=null;state.polls=0;say('任务已受理。实际写入仍需您核对预览。');
    });
  });
  input.addEventListener('keydown',event=>{if(!event.isComposing&&event.keyCode!==229&&event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();$('#assistant-composer').requestSubmit();}});
  input.addEventListener('input',()=>{rememberDraft();sizeInput();updateControls();});
  latest.addEventListener('click',jumpLatest);
  messages.addEventListener('scroll',()=>{followLatest=messages.scrollHeight-messages.scrollTop-messages.clientHeight<40;if(followLatest)latest.hidden=true;},{passive:true});
  messages.addEventListener('toggle',event=>{if(event.target.tagName==='DETAILS'&&event.target.open)followLatest=false;},true);
  list.addEventListener('change',()=>{resetConversation(list.value);loadConversation();});
  $('[data-assistant-new]').addEventListener('click',()=>{resetConversation();list.value='';input.focus();say('新会话将在发送第一条消息时创建。');});
  archived.addEventListener('change',()=>{resetConversation();bootstrap();});
  archive.addEventListener('click',async()=>{
    if(!state.conversation)return;
    const item=state.conversation;
    if(!await window.OfficeUI.confirm(item.archived?'恢复后可以继续此会话。':'归档保留任务与回执，之后可以恢复。',{title:item.archived?'恢复会话？':'归档会话？'}))return;
    await withAction(()=>api.action('archive',{identifier:item.id,version:item.version,archived:!item.archived}));
    if(!state.blocked){resetConversation();await bootstrap();}
  });
  $('#assistant-search').addEventListener('submit',event=>{event.preventDefault();bootstrap();});
  query.addEventListener('search',()=>{if(!query.value)bootstrap();});
  rename.addEventListener('click',()=>{
    const item=state.conversation;if(!item)return;
    const form=view.el('form'),label=view.el('label','会话标题'),field=view.el('input'),hint=view.el('p',undefined,'small muted');
    field.value=item.title;field.maxLength=100;field.required=true;field.id='assistant-rename-title';label.htmlFor=field.id;
    const submit=view.button('保存标题',()=>form.requestSubmit(),'primary');submit.type='button';form.append(label,field,hint,submit);
    form.addEventListener('submit',async event=>{event.preventDefault();if(!field.value.trim()){hint.textContent='请输入标题';return;}submit.disabled=true;form.setAttribute('aria-busy','true');
      await withAction(async()=>{const updated=await api.action('rename',{identifier:item.id,version:item.version,title:field.value.trim()});const option=Array.from(list.options).find(row=>row.value===item.id);if(option)option.textContent=updated.title;state.conversation=updated;state.version=updated.version;});
      form.removeAttribute('aria-busy');submit.disabled=false;
      if(state.actionError)hint.textContent=state.actionError;else window.OfficeUI.closeRecord();
    });
    if(window.OfficeUI.present('重命名会话',form))field.focus();
  });
  root.querySelectorAll('[data-assistant-starter]').forEach(button=>button.addEventListener('click',async()=>{
    if(input.value.trim()&&!await window.OfficeUI.confirm('替换当前尚未发送的草稿？',{title:'填入示例',accept:'替换草稿'}))return;
    input.value=button.dataset.assistantStarter;rememberDraft();sizeInput();updateControls();input.focus();
  }));
  if(!full){
    try{setWidth(Number(JSON.parse(sessionStorage.getItem(storeKey)||'{}').width)||420);}catch(_){}
    resize.addEventListener('pointerdown',event=>{if(event.button!==0)return;resize.setPointerCapture(event.pointerId);event.preventDefault();});
    resize.addEventListener('pointermove',event=>{if(resize.hasPointerCapture(event.pointerId))setWidth(innerWidth-event.clientX);});
    resize.addEventListener('pointerup',event=>{if(resize.hasPointerCapture(event.pointerId)){resize.releasePointerCapture(event.pointerId);save();}});
    resize.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();setWidth(event.key==='Home'?360:event.key==='End'?640:width+(event.key==='ArrowLeft'?20:-20));save();}});
    window.addEventListener('resize',()=>setWidth(width));
  }
  more.addEventListener('click',()=>bootstrap(true));earlier.addEventListener('click',()=>loadConversation(state.next));
  const reload=view.button('重新加载',()=>{if(state.blocked){location.reload();return;}state.polls=0;state.failures=0;state.blocked=false;state.actionError='';bootstrap();});reload.classList.add('small');$('.assistant-historybar').append(reload);
  function open() {
    if(!full&&innerWidth<1200){location.assign('/console/assistant/workspace');return;}
    root.hidden=false;document.body.classList.toggle('assistant-open',!full);launch?.setAttribute('aria-expanded','true');save();bootstrap();input.focus();
  }
  function close() {if(full)return;clearPoll();document.documentElement.classList.remove('assistant-preopen');root.hidden=true;document.body.classList.remove('assistant-open');launch?.setAttribute('aria-expanded','false');save();launch?.focus();}
  launch?.addEventListener('click',()=>root.hidden?open():close());$('[data-assistant-close]').addEventListener('click',close);
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&root.contains(document.activeElement)&&!document.querySelector('dialog[open]')&&!root.hidden&&!full){event.preventDefault();close();}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)clearPoll();else if(!root.hidden){state.polls=0;loadConversation();}});
  window.addEventListener('beforeunload',event=>{rememberDraft();if(Array.from(drafts.values()).some(value=>value.trim())){event.preventDefault();event.returnValue='';}clearPoll();});
  window.addEventListener('pagehide',clearPoll);
  window.addEventListener('pageshow',event=>{if(event.persisted&&!root.hidden){state.polls=0;bootstrap();}});
  let wasOpen=false;try {wasOpen=JSON.parse(sessionStorage.getItem(storeKey)||'{}').open;}catch(_){}
  if(full)open();else if(wasOpen&&innerWidth>=1200)open();
})();
