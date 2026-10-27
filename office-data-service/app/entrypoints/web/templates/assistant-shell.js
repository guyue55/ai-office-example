/* 会话仅保存ID／展开／宽度偏好；草稿、回执和批准只在当前页面内存。 */
(() => {
  const api=window.OfficeAssistant, ui=window.OfficeUI;
  if(!api||!ui)return;
  const {root,view}=api,full=root.dataset.full==='true';
  const $=selector=>root.querySelector(selector),launch=document.getElementById('assistant-launch');
  const messages=$('#assistant-messages'),input=$('#assistant-input'),send=$('[data-assistant-send]'),feedback=$('#assistant-feedback');
  const list=$('#assistant-conversations'),query=$('#assistant-query'),archived=$('#assistant-archived'),more=$('[data-assistant-more]');
  const archive=$('[data-assistant-archive]'),rename=$('[data-assistant-rename]'),earlier=$('[data-assistant-earlier]'),latest=$('[data-assistant-latest]'),resize=$('.assistant-resize');
  const historyStatus=$('#assistant-history-status'),reconnect=$('[data-assistant-reconnect]'),context=$('#assistant-context');
  const storeKey='office-assistant:'+root.dataset.owner,drafts=new Map(),rendered=new Map();
  const pages={dashboard:'仪表盘',employees:'人员管理',roles:'角色','permission-sets':'权限集',permissions:'权限目录',audit:'审计','client-authorizations':'客户端授权',assistant:'智能助手'};
  const path=location.pathname.split('/')[2],page=Object.hasOwn(pages,path)?path:'';
  const state={id:'',conversation:null,turns:[],conversations:new Map(),next:'',listNext:'',ready:false,loading:false,busy:false,failures:0,polls:0,timer:null,blocked:false,version:0,epoch:'',intent:null,actionError:''};
  let width=420,followLatest=true,lastAnnouncement='';
  // 仅全覆盖视图按模态处理；桌面并排侧栏保留原页面可操作性。
  const covered=matchMedia('(max-width:700px)'),backgroundState=new Map();
  function modalBoundary(){
    const modal=!full&&!root.hidden&&covered.matches;
    if(modal){
      for(const node of document.querySelectorAll('main,.shell-nav,.mobile-shell,.skip-link')){
        if(!backgroundState.has(node))backgroundState.set(node,node.inert);
        node.inert=true;
      }
      root.setAttribute('role','dialog');root.setAttribute('aria-modal','true');
    }else{
      for(const [node,inert] of backgroundState)node.inert=inert;
      backgroundState.clear();root.removeAttribute('role');root.removeAttribute('aria-modal');
    }
    document.body.classList.toggle('assistant-modal-open',modal);
    if(modal&&!root.contains(document.activeElement))input.focus({preventScroll:true});
  }
  covered.addEventListener('change',modalBoundary);
  document.addEventListener('keydown',event=>{
    if(event.key!=='Tab'||event.defaultPrevented||!root.matches('[aria-modal=true]')||document.querySelector('dialog[open],[role=dialog]:popover-open'))return;
    const nodes=Array.from(root.querySelectorAll('button,a[href],input,select,textarea,[tabindex]')).filter(node=>!node.disabled&&node.tabIndex>=0&&!node.closest('[hidden],[inert]')&&node.getClientRects().length);
    const first=nodes[0],last=nodes.at(-1);
    if(!first){event.preventDefault();return;}
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  });
  try{state.id=JSON.parse(sessionStorage.getItem(storeKey)||'{}').id||'';}catch(_){}
  ui.decorateIcon($('[data-assistant-menu]'),'more','更多会话操作');
  ui.decorateIcon($('[data-assistant-close]'),'collapse','收起智能助手');
  ui.decorateIcon($('[data-assistant-context-remove]'),'close','取消关联当前页面');
  root.querySelectorAll('[data-assistant-help]').forEach(node=>{if(node.closest('.assistant-footnote'))ui.decorateIcon(node,'info','使用说明与快捷键');});
  ui.decorateIcon($('[data-floating-close]'),'close','关闭历史会话');
  ui.decorateIcon(latest,'down','回到最新消息');
  const historyPopup=ui.floating($('[data-assistant-history]'),$('#assistant-history-panel'),{initialFocus:()=>query});
  const optionsPopup=ui.floating($('[data-assistant-menu]'),$('#assistant-options-panel'));
  function save(){try{sessionStorage.setItem(storeKey,JSON.stringify({id:state.id,open:full||!root.hidden,width}));}catch(_){}}
  function say(text,{visible=false,error=false,retry=false,key=''}={}){
    if(feedback.textContent!==text||key&&key!==lastAnnouncement)feedback.replaceChildren(document.createTextNode(text));
    lastAnnouncement=key;
    feedback.classList.toggle('sr-only',!visible);feedback.classList.toggle('is-error',error);reconnect.hidden=!retry;
  }
  function announceTurn(){
    const turn=state.turns.at(-1);
    if(!turn){say('会话已同步。');return;}
    const phase=turn.result?.outcome_unknown?'outcome_unknown':turn.status;
    const terminal=view.outcomes;
    const text=terminal[phase]||(turn.cancel_requested?'已请求停止；已完成操作不会撤回。':turn.pending?('decision' in turn.pending?'决定已记录，等待执行回执。':'有操作等待您核对，批准后才会继续。'):turn.write_inflight?'正在执行已批准操作，结果以回执为准。':phase==='queued'?'任务已排队，尚未开始。':'任务正在处理。');
    const key=[state.id,turn.id,phase,!!turn.cancel_requested,!!turn.write_inflight,turn.pending?.ticket||'',turn.pending?.decision??''].join(':');
    say(text,{key,visible:!!terminal[phase]&&phase!=='completed',error:phase==='failed'});
  }
  function clearPoll(){clearTimeout(state.timer);state.timer=null;}
  function rememberDraft(){drafts.set(state.id,input.value);}
  function sizeInput(){input.style.height='auto';input.style.height=Math.min(180,Math.max(48,input.scrollHeight))+'px';}
  function restoreDraft(){input.value=drafts.get(state.id)||'';sizeInput();}
  function setWidth(value){const maximum=Math.max(360,Math.min(640,innerWidth-800));width=Math.min(maximum,Math.max(360,value));resize.setAttribute('aria-valuemax',String(maximum));resize.setAttribute('aria-valuenow',String(width));resize.setAttribute('aria-valuetext',width+'像素');const choice=$('#assistant-width');if(choice){choice.value=[360,420,640].includes(width)?String(width):'';choice.querySelector('[value=""]').textContent='自定义（'+width+'像素）';}document.documentElement.style.setProperty('--assistant-width',width+'px');}
  function jumpLatest(){followLatest=true;messages.scrollTop=messages.scrollHeight;latest.hidden=true;}
  function showError(error){
    state.failures++;
    const text=error.message+(error.trace?' 追踪号：'+error.trace:'');
    state.actionError=text;say(text,{visible:true,error:true,retry:true});
    if([401,403].includes(error.status)){
      state.blocked=true;state.turns=[];state.conversation=null;state.conversations.clear();drafts.clear();input.value='';rendered.clear();clearPoll();
      ui.closePopovers();list.replaceChildren();messages.replaceChildren(view.el('p','当前身份或权限不可用，请重新登录或联系管理员。','assistant-warning'));
    }
  }
  function updateContext(){
    context.checked=context.checked&&!!page;
    $('#assistant-page-label').textContent=pages[page]||'';
    $('#assistant-context-chip').hidden=!context.checked;
    $('[data-assistant-context-add]').hidden=context.checked||!page;
  }
  function updateControls(){
    const working=state.turns.some(turn=>view.active.has(turn.status));
    const reason=state.blocked?'当前身份或权限不可用':!state.ready?'助手尚未就绪':state.busy?'正在提交操作':state.loading?'正在同步会话':state.conversation?.archived?'请先恢复归档会话':working?'本轮任务仍在进行，可以先起草下一条':!input.value.trim()?'请输入工作要求':'';
    send.disabled=!!reason;input.disabled=state.blocked;
    input.placeholder=state.ready?'描述您希望完成的工作……':'可先起草，助手就绪后才能发送。';
    const hint=$('#assistant-input-hint');hint.textContent=!state.ready&&!state.blocked?'当前不能发送；可在此保留本页草稿。':reason||'Enter 换行 · Ctrl / ⌘ + Enter 发送';
    hint.classList.toggle('sr-only',state.ready&&!working&&!state.blocked&&!state.conversation?.archived);
    rename.disabled=state.busy||state.loading||!state.conversation||state.blocked;
    archive.disabled=rename.disabled||working;archive.textContent=state.conversation?.archived?'恢复会话':'归档会话';
    $('[data-assistant-new]').disabled=state.busy||state.loading||state.blocked;
    $('#assistant-search button').disabled=state.busy||state.loading||state.blocked;archived.disabled=state.busy||state.loading||state.blocked;more.disabled=state.busy||state.loading||state.blocked;
    list.querySelectorAll('button').forEach(node=>{node.disabled=state.busy||state.loading||state.blocked;});
    $('#assistant-topic').textContent=state.conversation?.title||(state.id?'正在加载会话':'新会话');
    $('#assistant-topic').title=state.conversation?.title||'';
    updateContext();
  }
  function renderHistory(){
    list.replaceChildren();
    for(const item of state.conversations.values()){
      const row=view.el('div',undefined,'assistant-conversation-row');row.setAttribute('role','listitem');
      const button=view.button('',()=>{historyPopup.close();resetConversation(item.id);loadConversation();},'assistant-conversation');
      button.dataset.conversation=item.id;button.setAttribute('aria-label','打开会话：'+item.title);if(item.id===state.id)button.setAttribute('aria-current','true');
      button.append(view.el('span',item.title,'assistant-conversation-title'),view.el('span',new Date(item.updated_at).toLocaleString('zh-CN')+(item.archived?' · 已归档':''),'small muted'));row.append(button);list.append(row);
    }
    historyStatus.textContent=state.conversations.size?'已显示 '+state.conversations.size+' 个会话'+(state.listNext?'，可继续加载。':'。'):(query.value.trim()?'没有匹配的会话。':archived.checked?'暂无归档会话。':'暂无历史会话。');
    more.hidden=!state.listNext;updateControls();
  }
  function paint(){
    const nearBottom=messages.scrollHeight-messages.scrollTop-messages.clientHeight<90;
    const openPreview=!!messages.querySelector('details[open]'),selection=getSelection();
    const selecting=selection&&!selection.isCollapsed&&messages.contains(selection.anchorNode);
    const anchor=Array.from(messages.children).find(node=>node.getBoundingClientRect().bottom>messages.getBoundingClientRect().top),anchorTop=anchor?.getBoundingClientRect().top;
    const ids=new Set(state.turns.map(turn=>turn.id));messages.querySelectorAll('[data-placeholder]').forEach(node=>node.remove());
    for(const [id,entry] of rendered){if(!ids.has(id)){entry.node.remove();rendered.delete(id);}}
    let changed=false;
    for(const turn of state.turns){
      const signature=JSON.stringify(turn),entry=rendered.get(turn.id);if(entry?.signature===signature)continue;
      const node=view.renderTurn(turn,controls);
      if(entry){
        const expanded=new Map(Array.from(entry.node.querySelectorAll('details')).map((item,index)=>[item.dataset.detailKey||index,item.open]));
        const focused=entry.node.contains(document.activeElement)?document.activeElement.getAttribute('aria-label')||document.activeElement.textContent:null;
        entry.node.replaceWith(node);node.querySelectorAll('details').forEach((item,index)=>{if(expanded.has(item.dataset.detailKey||index))item.open=expanded.get(item.dataset.detailKey||index);});
        if(focused)Array.from(node.querySelectorAll('button')).find(item=>(item.getAttribute('aria-label')||item.textContent)===focused)?.focus({preventScroll:true});
      }
      rendered.set(turn.id,{node,signature});changed=true;
    }
    let cursor=messages.firstElementChild;
    state.turns.forEach(turn=>{const node=rendered.get(turn.id).node;if(node!==cursor)messages.insertBefore(node,cursor);cursor=node.nextElementSibling;});
    if(!state.turns.length)messages.append(view.renderWelcome(controls,{ready:state.ready,loading:state.loading}));
    earlier.hidden=!state.next||state.turns.length>=50;
    if(state.turns.length>=50&&state.next){const note=view.el('p','当前界面最多展示50轮；可通过有界接口继续查询服务器历史。','small muted');note.dataset.placeholder='true';messages.prepend(note);}
    if(followLatest&&nearBottom&&!openPreview&&!selecting)jumpLatest();
    else{if(anchor?.isConnected)messages.scrollTop+=anchor.getBoundingClientRect().top-anchorTop;if(changed)latest.hidden=false;}
    updateControls();
  }
  function schedule(){
    clearPoll();if(root.hidden||document.hidden||state.blocked||!state.id||state.polls>=120||state.failures>=4)return;
    if(state.turns.some(turn=>view.active.has(turn.status)))state.timer=setTimeout(()=>loadConversation(),Math.min(15000,2000*2**state.failures));
  }
  async function loadConversation(before=''){
    if(state.loading||state.busy||!state.id||state.blocked)return;
    if(document.querySelector('dialog[open]')){schedule();return;}
    state.loading=true;updateControls();const id=state.id;
    try{
      const item=await api.get('conversations/'+encodeURIComponent(id)+(before?'?before='+encodeURIComponent(before):''));if(id!==state.id)return;
      const changed=state.epoch!==item.access_fingerprint||state.version!==item.version||JSON.stringify(state.turns.slice(-10))!==JSON.stringify(item.turns);
      if(state.epoch&&state.epoch!==item.access_fingerprint)state.turns=[];
      if(before){const height=messages.scrollHeight,offset=messages.scrollTop;state.turns=[...item.turns,...state.turns].slice(-50);state.next=item.next;paint();messages.scrollTop=offset+messages.scrollHeight-height;}
      else{const previous=new Map(state.turns.map(turn=>[turn.id,turn]));item.turns.forEach(turn=>previous.set(turn.id,turn));state.turns=Array.from(previous.values()).sort((a,b)=>a.created_at.localeCompare(b.created_at)||a.id.localeCompare(b.id)).slice(-50);if(!state.conversation||state.turns.length<=item.turns.length)state.next=item.next;}
      state.conversation=item;state.epoch=item.access_fingerprint;state.version=item.version;state.failures=0;state.polls++;
      if(state.conversations.has(item.id))state.conversations.set(item.id,item);
      if(changed&&!before)paint();else updateControls();
      if(state.actionError)say(state.actionError,{visible:true,error:true,retry:true});
      else if(item.history_reset)say('权限配置已变化，旧内容已隐藏；新问题仅使用当前可用信息。',{visible:true});
      else if(item.archived)say('此会话已归档；可从更多会话操作中恢复。',{visible:true});
      else announceTurn();
      if(state.polls>=120)say('已暂停自动更新，请重新连接核对任务。',{visible:true,retry:true});
    }catch(error){showError(error);}finally{state.loading=false;updateControls();schedule();}
  }
  async function bootstrap(append=false){
    if(state.busy||state.loading||state.blocked)return;
    state.loading=true;updateControls();historyStatus.textContent='正在加载会话…';
    try{
      const data=await api.get('bootstrap?archived='+archived.checked+'&q='+encodeURIComponent(query.value.trim())+(append?'&after='+encodeURIComponent(state.listNext):''));
      state.ready=data.capability.ready;if(!append)state.conversations.clear();data.conversations.items.forEach(item=>state.conversations.set(item.id,item));state.listNext=data.conversations.next||'';renderHistory();
      if(!state.ready)say(data.capability.message||'智能助手尚未启用，请联系管理员配置。',{visible:true});
      else if(!state.actionError)say('会话列表已更新。');
    }catch(error){historyStatus.textContent='会话加载失败，请重新搜索或重新连接。';showError(error);}finally{state.loading=false;updateControls();}
    if(state.id&&!append&&!query.value.trim())await loadConversation();else if(!state.id)paint();
  }
  function resetConversation(id=''){
    rememberDraft();clearPoll();rendered.clear();messages.replaceChildren();followLatest=true;state.id=id;restoreDraft();state.conversation=null;state.turns=[];state.next='';state.version=0;state.epoch='';state.polls=0;state.failures=0;state.intent=null;state.actionError='';save();paint();
  }
  async function withAction(fn){
    if(state.busy||state.blocked)return false;
    if(state.loading){say('正在同步状态，请稍后再操作。',{visible:true});return false;}
    clearPoll();state.busy=true;updateControls();let success=false;
    try{await fn();state.failures=0;state.actionError='';success=true;}catch(error){showError(error);}finally{state.busy=false;updateControls();}
    if(state.id)await loadConversation();return success;
  }
  const controls={
    pageName:code=>pages[code]||'未关联页面',
    async fillDraft(prompt){if(input.value.trim()&&!await ui.confirm('替换当前尚未发送的草稿？',{title:'填入示例',accept:'替换草稿'}))return;input.value=prompt;rememberDraft();sizeInput();updateControls();input.focus();},
    showDetails(turn){const box=view.el('div');box.append(view.fields({status:view.labels.statuses[turn.status]||turn.status,created_at:turn.created_at,updated_at:turn.updated_at,version:turn.version}));if(turn.result?.usage)box.append(view.detail('模型用量',turn.result.usage));const link=view.el('a','打开完整任务记录');link.href='/console/assistant/'+encodeURIComponent(turn.id);box.append(link);ui.present('任务详情',box);},
    async refreshBusiness(){const main=document.querySelector('main');if(window.OfficeForms?.dirty(main)&&!await ui.confirm('当前页面有未保存的修改。刷新会丢失这些内容，是否继续？',{title:'刷新业务页面？',accept:'放弃修改并刷新'}))return;location.reload();},
    async decide(turn,approve){if(!await ui.confirm(approve?'请核对本次完整预览。批准仅适用于此操作，后续写入仍需确认。':'拒绝本次操作？助手不会执行该提案。',{title:approve?'批准本次操作？':'拒绝操作',accept:approve?'批准本次操作':'拒绝操作'}))return;await withAction(()=>api.action('decide',{identifier:turn.id,version:turn.version,proposal_id:turn.pending.ticket,approve}));},
    async stop(turn){if(!await ui.confirm('已完成的操作不会撤回，在途写入可能需要核对实际数据。',{title:'停止任务？',accept:'停止任务'}))return;await withAction(()=>api.action('cancel',{identifier:turn.id,version:turn.version}));}
  };
  $('#assistant-composer').addEventListener('submit',async event=>{
    event.preventDefault();if(send.disabled||!input.value.trim())return;const prompt=input.value.trim();
    if(state.intent?.prompt!==prompt||state.intent?.id!==state.id)state.intent={prompt,id:state.id,key:Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join(''),page:context.checked?page:''};
    const request_key=state.intent.key;
    await withAction(async()=>{
      if(!state.id){const item=await api.action('create',{title:prompt.slice(0,40)}),intent=state.intent,draft=input.value;resetConversation(item.id);input.value=draft;rememberDraft();sizeInput();state.intent=intent;state.intent.id=item.id;}
      await api.action('start',{prompt,request_key,conversation_id:state.id,context_page:state.intent.page});
      if(input.value.trim()===prompt){input.value='';drafts.delete(state.id);}else rememberDraft();drafts.delete('');sizeInput();state.intent=null;state.polls=0;say('任务已受理。');
    });
  });
  input.addEventListener('keydown',event=>{if(!event.isComposing&&event.keyCode!==229&&event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();$('#assistant-composer').requestSubmit();}});
  input.addEventListener('input',()=>{rememberDraft();sizeInput();updateControls();});
  latest.addEventListener('click',jumpLatest);
  messages.addEventListener('scroll',()=>{followLatest=messages.scrollHeight-messages.scrollTop-messages.clientHeight<40;if(followLatest)latest.hidden=true;},{passive:true});
  messages.addEventListener('toggle',event=>{if(event.target.tagName==='DETAILS'&&event.target.open)followLatest=false;},true);
  $('[data-assistant-new]').addEventListener('click',()=>{const running=state.turns.some(turn=>view.active.has(turn.status));resetConversation();input.focus();say(running?'新会话已准备，原任务仍可在任务记录中核对。':'新会话将在首次发送时创建。',{visible:running});});
  $('#assistant-search').addEventListener('submit',event=>{event.preventDefault();state.actionError='';bootstrap();});
  query.addEventListener('search',()=>{if(!query.value)bootstrap();});
  archived.addEventListener('change',()=>{state.actionError='';bootstrap();});
  more.addEventListener('click',()=>bootstrap(true));earlier.addEventListener('click',()=>loadConversation(state.next));
  $('[data-assistant-history]').addEventListener('click',()=>{if(historyPopup.open)bootstrap();});
  $('[data-assistant-context-remove]').addEventListener('click',()=>{context.checked=false;updateContext();input.focus();});
  $('[data-assistant-context-add]').addEventListener('click',()=>{context.checked=!!page;updateContext();input.focus();});
  archive.addEventListener('click',async()=>{
    const item=state.conversation;if(!item)return;
    if(!await ui.confirm(item.archived?'恢复后可以继续此会话。':'归档保留任务与回执，之后可以恢复。',{title:item.archived?'恢复会话？':'归档会话？'}))return;
    if(await withAction(()=>api.action('archive',{identifier:item.id,version:item.version,archived:!item.archived}))){resetConversation();await bootstrap();}
  });
  rename.addEventListener('click',()=>{
    const item=state.conversation;if(!item)return;
    const form=view.el('form'),label=view.el('label','会话标题'),field=view.el('input'),hint=view.el('p',undefined,'small muted');field.value=item.title;field.maxLength=100;field.required=true;field.id='assistant-rename-title';label.htmlFor=field.id;
    const submit=view.button('保存标题',()=>form.requestSubmit(),'primary');form.append(label,field,hint,submit);
    form.addEventListener('submit',async event=>{event.preventDefault();if(!field.value.trim()){hint.textContent='请输入标题';return;}submit.disabled=true;form.setAttribute('aria-busy','true');
      const success=await withAction(async()=>{const updated=await api.action('rename',{identifier:item.id,version:item.version,title:field.value.trim()});state.conversation=updated;state.version=updated.version;if(state.conversations.has(updated.id))state.conversations.set(updated.id,updated);});
      form.removeAttribute('aria-busy');submit.disabled=false;
      if(success){await ui.closeRecord();await loadConversation();}else hint.textContent=state.actionError||'正在同步，请稍后核对。';
    });if(ui.present('重命名会话',form))field.focus();
  });
  root.querySelectorAll('[data-assistant-help]').forEach(node=>node.addEventListener('click',()=>{
    const box=view.el('div');box.append(view.el('p','Enter 换行，Ctrl / ⌘ + Enter 发送。中文输入法组合期间不会触发发送。'),view.el('p','页面关联只说明讨论位置，不会自动上传整页或隐藏字段。仅提交您获准使用的资料。'),view.el('p','写入前请核对完整预览。收起不会停止任务；停止不会撤回已完成操作。'),view.el('p','草稿仅保存在当前页面内存，刷新后不恢复。续问使用有界历史，权限变化时旧内容可能隐藏。'));ui.present('使用说明与快捷键',box);
  }));
  async function sync(){if(state.blocked){location.reload();return;}state.polls=0;state.failures=0;state.actionError='';if(state.id)await loadConversation();else await bootstrap();}
  $('[data-assistant-sync]').addEventListener('click',sync);reconnect.addEventListener('click',sync);
  if(!full){
    try{setWidth(Number(JSON.parse(sessionStorage.getItem(storeKey)||'{}').width)||420);}catch(_){}
    $('#assistant-width').addEventListener('change',event=>{setWidth(Number(event.target.value));save();});
    resize.addEventListener('pointerdown',event=>{if(event.button!==0)return;resize.setPointerCapture(event.pointerId);event.preventDefault();});
    resize.addEventListener('pointermove',event=>{if(resize.hasPointerCapture(event.pointerId))setWidth(innerWidth-event.clientX);});
    resize.addEventListener('pointerup',event=>{if(resize.hasPointerCapture(event.pointerId)){resize.releasePointerCapture(event.pointerId);save();}});
    resize.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();setWidth(event.key==='Home'?360:event.key==='End'?640:width+(event.key==='ArrowLeft'?20:-20));save();}});
    window.addEventListener('resize',()=>setWidth(width));
  }
  function open(){root.hidden=false;modalBoundary();document.body.classList.toggle('assistant-open',!full);launch?.setAttribute('aria-expanded','true');save();bootstrap();sizeInput();input.focus();}
  function close(){if(full)return;ui.closePopovers();clearPoll();document.documentElement.classList.remove('assistant-preopen');root.hidden=true;modalBoundary();document.body.classList.remove('assistant-open');launch?.setAttribute('aria-expanded','false');save();launch?.focus();}
  launch?.addEventListener('click',()=>root.hidden?open():close());$('[data-assistant-close]').addEventListener('click',close);
  document.addEventListener('keydown',event=>{if(!event.defaultPrevented&&event.key==='Escape'&&root.contains(document.activeElement)&&!document.querySelector('dialog[open]')&&!root.hidden&&!full){event.preventDefault();close();}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)clearPoll();else if(!root.hidden){state.polls=0;loadConversation();}});
  // 用户可能取消离页；轮询清理只在实际pagehide执行，不能提前停在旧状态。
  window.addEventListener('beforeunload',event=>{rememberDraft();if(Array.from(drafts.values()).some(value=>value.trim())){event.preventDefault();event.returnValue='';}});
  window.addEventListener('pagehide',clearPoll);window.addEventListener('pageshow',event=>{if(event.persisted&&!root.hidden){state.polls=0;bootstrap();}});
  let wasOpen=false;try{wasOpen=JSON.parse(sessionStorage.getItem(storeKey)||'{}').open;}catch(_){}
  updateContext();if(full)open();else if(wasOpen&&innerWidth>=1200)open();
})();
