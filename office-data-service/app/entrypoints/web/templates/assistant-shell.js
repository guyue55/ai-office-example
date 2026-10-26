/* 会话ID与展开偏好按账号在本标签页保存；正文、回执和审批不写浏览器持久存储。 */
(() => {
  const api=window.OfficeAssistant;
  if (!api) return;
  const {root,view}=api, full=root.dataset.full==='true';
  const $=selector=>root.querySelector(selector), launch=document.getElementById('assistant-launch');
  const list=$('#assistant-conversations'), messages=$('#assistant-messages'), input=$('#assistant-input'), send=$('[data-assistant-send]'), feedback=$('#assistant-feedback');
  const archived=$('#assistant-archived'), archive=$('[data-assistant-archive]'), more=$('[data-assistant-more]'), earlier=$('[data-assistant-earlier]');
  const storeKey='office-assistant:'+root.dataset.owner;
  const pages={dashboard:'仪表盘',employees:'人员管理',roles:'角色', 'permission-sets':'权限集', permissions:'权限目录',audit:'审计','client-authorizations':'客户端授权',assistant:'智能助手'};
  const path=location.pathname.split('/')[2], page=Object.hasOwn(pages,path) ? path : '';
  $('#assistant-page-label').textContent=pages[page] || '未关联';
  const state={id:'',conversation:null,turns:[],next:'',listNext:'',ready:false,loading:false,busy:false,failures:0,polls:0,timer:null,blocked:false,version:0,epoch:0};
  try {state.id=JSON.parse(sessionStorage.getItem(storeKey)||'{}').id||'';} catch (_) { /* 存储不可用时仍可使用。 */ }
  function save() {try {sessionStorage.setItem(storeKey,JSON.stringify({id:state.id,open:full||!root.hidden}));} catch (_) {}}
  function say(text,error=false) {if(feedback.textContent!==text)feedback.textContent=text; feedback.classList.toggle('is-error',error);}
  function clearPoll() {clearTimeout(state.timer);state.timer=null;}
  function showError(error) {
    state.failures++;
    say(error.message+(error.trace ? ' 追踪号：'+error.trace : ''),true);
    if ([401,403].includes(error.status)) {
      state.blocked=true; state.turns=[];state.conversation=null;list.replaceChildren(new Option('当前身份或权限不可用','')); messages.replaceChildren(view.el('p','当前身份或权限不可用，请重新登录或联系管理员。','hint'));
      clearPoll();
    }
  }
  function updateControls() {
    const working=state.turns.some(turn=>view.active.has(turn.status));
    send.disabled=!state.ready||state.busy||state.loading||state.blocked||working||!!state.conversation?.archived;
    input.disabled=state.blocked;
    archive.disabled=state.busy||state.loading||!state.conversation||working||state.blocked;
    archive.textContent=state.conversation?.archived ? '恢复' : '归档';
    list.disabled=state.busy||state.loading; $('[data-assistant-new]').disabled=state.busy||state.loading||state.blocked;
  }
  function paint() {
    const nearBottom=messages.scrollHeight-messages.scrollTop-messages.clientHeight<90;
    const offset=messages.scrollTop;
    const expanded=new Set(Array.from(messages.querySelectorAll('[data-run] details[open]')).map(node=>node.closest('[data-run]').dataset.run+':'+Array.from(node.closest('[data-run]').querySelectorAll('details')).indexOf(node)));
    const focused=document.activeElement?.closest('[data-run]');
    const focusRun=focused?.dataset.run, focusText=focused ? document.activeElement.textContent : null;
    messages.replaceChildren(...state.turns.map(turn=>view.renderTurn(turn,controls)));
    if (!state.turns.length) messages.append(view.el('p','开始一个工作要求，或选择其他会话继续。','empty'));
    earlier.hidden=!state.next||state.turns.length>=50;
    if(state.turns.length>=50&&state.next)messages.prepend(view.el('p','当前界面最多展示50轮；历史仍保存在服务器，可通过有界接口继续查询。','small muted'));
    messages.querySelectorAll('[data-run]').forEach(article=>article.querySelectorAll('details').forEach((node,index)=>{if(expanded.has(article.dataset.run+':'+index))node.open=true;}));
    if (nearBottom) messages.scrollTop=messages.scrollHeight; else messages.scrollTop=offset;
    if (focusRun) {
      const article=Array.from(messages.querySelectorAll('[data-run]')).find(node=>node.dataset.run===focusRun);
      Array.from(article?.querySelectorAll('button')||[]).find(node=>node.textContent===focusText)?.focus({preventScroll:true});
    }
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
      if (before) {const height=messages.scrollHeight; state.turns=[...item.turns,...state.turns].slice(-50);state.next=item.next;paint();messages.scrollTop=messages.scrollHeight-height;}
      else {
        const previous=new Map(state.turns.map(turn=>[turn.id,turn])); item.turns.forEach(turn=>previous.set(turn.id,turn));
        state.turns=Array.from(previous.values()).sort((a,b)=>a.created_at.localeCompare(b.created_at)||a.id.localeCompare(b.id)).slice(-50);
        if (!state.conversation || state.turns.length<=item.turns.length) state.next=item.next;
      }
      if(!Array.from(list.options).some(option=>option.value===item.id))list.append(new Option(item.title+(item.archived?'（已归档）':''),item.id));
      list.value=item.id;
      state.conversation=item;state.epoch=item.access_fingerprint;state.version=item.version; state.failures=0;state.polls++;
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
      const data=await api.get('bootstrap?archived='+archived.checked+(append?'&after='+encodeURIComponent(state.listNext):''));
      state.ready=data.capability.ready;
      if (!append) list.replaceChildren(new Option('选择会话',''));
      data.conversations.items.forEach(item=>{const option=new Option(item.title,item.id);list.append(option);});
      state.listNext=data.conversations.next||'';more.hidden=!state.listNext;list.value=state.id;
      say(state.ready ? '选择会话或直接输入工作要求。' : (data.capability.message||'智能助手尚未启用，请联系管理员配置。'));
    } catch(error) {showError(error);} finally {state.loading=false;updateControls();}
    if(state.id&&!append) await loadConversation();
  }
  function resetConversation(id='') {clearPoll();state.id=id;state.conversation=null;state.turns=[];state.next='';state.version=0;state.epoch=0;state.polls=0;state.failures=0;state.intent=null;state.actionError='';save();paint();}
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
    if(state.intent?.prompt!==prompt||state.intent?.id!==state.id)state.intent={prompt,id:state.id,key:crypto.randomUUID(),page:$('#assistant-context').checked?page:''};
    const request_key=state.intent.key;
    await withAction(async()=>{
      if(!state.id) {const item=await api.action('create',{title:prompt.slice(0,40)});const intent=state.intent;resetConversation(item.id);state.intent=intent;list.append(new Option(item.title,item.id));list.value=item.id;state.intent.id=item.id;}
      await api.action('start',{prompt,request_key,conversation_id:state.id,context_page:state.intent.page});
      input.value='';state.intent=null;state.polls=0;say('任务已受理。实际写入仍需您核对预览。');
    });
  });
  input.addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();$('#assistant-composer').requestSubmit();}});
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
  window.addEventListener('beforeunload',event=>{if(input.value.trim()){event.preventDefault();event.returnValue='';}clearPoll();});
  window.addEventListener('pagehide',clearPoll);
  window.addEventListener('pageshow',event=>{if(event.persisted&&!root.hidden){state.polls=0;bootstrap();}});
  let wasOpen=false;try {wasOpen=JSON.parse(sessionStorage.getItem(storeKey)||'{}').open;}catch(_){}
  if(full)open();else if(wasOpen&&innerWidth>=1200)open();
})();
