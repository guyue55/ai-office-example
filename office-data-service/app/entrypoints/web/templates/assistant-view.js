/* 同一渲染器服务侧栏与工作区；用户文本保持原文；模型说明经受限Markdown渲染。 */
(() => {
  const api = window.OfficeAssistant;
  if (!api) return;
  const labels = JSON.parse(document.getElementById('assistant-labels').textContent);
  const active = new Set(['queued','running','awaiting_confirmation']);
  const outcomes=Object.fromEntries(Object.entries(labels.outcomes).filter(([phase])=>!active.has(phase)));
  const names = {name:'名称', username:'登录账号', email:'工作邮箱', mode:'凭证方式', role_ids:'角色标识', kind:'操作类型', identifier:'目标标识', remote_id:'登录账号标识', id:'编号', version:'版本', before:'变更前', after:'变更后', values:'字段值', operation:'操作', status:'状态', schema_revision:'字段定义版本', project_id:'数据容器编号', complete:'覆盖完整', next:'下一页', count:'数量', total:'总数', archived:'已归档', permissions:'权限', items:'数据项', warning:'提示', warnings:'提示', message:'说明', active:'启用'};
  function el(tag, text, cls) {const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node;}
  function button(text, action, kind='secondary') {const node = el('button', text, kind); node.type='button'; node.addEventListener('click', action); return node;}
  function fields(value) {
    const list = el('dl', undefined, 'assistant-fields');
    if (!value || typeof value !== 'object') {list.append(el('dt','内容'),el('dd',String(value ?? '—'))); return list;}
    let count=0;
    function walk(object, prefix='', depth=0) {
      Object.entries(object).forEach(([key,item])=>{
        if(count>=16)return;
        const name=(prefix ? prefix+' / ' : '')+(names[key]||key);
        if(item && !Array.isArray(item) && typeof item==='object' && depth<2 && Object.keys(item).length){walk(item,name,depth+1);return;}
        const display=typeof item==='boolean' ? (item?'是':'否') : item && typeof item==='object' ? JSON.stringify(item,null,2) : String(item??'—');
        list.append(el('dt',name),el('dd',display.length>500?display.slice(0,500)+'…（完整内容见详情）':display));count++;
      });
    }
    walk(value);
    return list;
  }
  function detail(title, value) {const node=el('details'); node.append(el('summary',title),el('pre',JSON.stringify(value,null,2))); return node;}
  const impacts={access:'权限配置变更：以服务端预览的对象、关联及版本为准。',account:'登录账号操作：请核对目标人员与账号；此处不展示密码。',account_resume:'继续原账号操作：只处理本次预览允许的步骤。',data:'数据管理变更：请核对容器、字段定义和版本。',submit_record:'提交候选数据：提交成功不等于业务已确认。',confirm_record:'确认当前版本：确认后冻结，更正需生成新版本。',correct_record:'创建更正候选：不覆盖原确认历史，需要重新确认。',describe_device:'登记当前授权设备备注，不改变授权权限。',change_authorization:'变更客户端授权：停用后旧凭证不可重新激活，请核对目标设备。'};
  function preview(pending){
    const box=el('div');box.append(el('p',impacts[pending.operation]||'请核对完整预览；摘要不能替代服务端校验。','small muted'));
    const value=pending.preview;
    if(value&&typeof value==='object'&&value.before&&value.after&&typeof value.before==='object'&&typeof value.after==='object'&&!Array.isArray(value.before)&&!Array.isArray(value.after)){
      const table=el('table'),head=el('thead'),tr=el('tr');['字段','变更前','变更后'].forEach(label=>tr.append(el('th',label)));head.append(tr);table.append(head);const body=el('tbody');
      const keys=Array.from(new Set([...Object.keys(value.before),...Object.keys(value.after)]));
      let shown=0;
      function display(object,key){if(!Object.hasOwn(object,key))return '未提供';const item=object[key];const text=typeof item==='object'?JSON.stringify(item):String(item??'空');return text.length>300?text.slice(0,300)+'…（见完整预览）':text;}
      keys.forEach(key=>{if(shown>=16||JSON.stringify(value.before[key])===JSON.stringify(value.after[key]))return;const row=el('tr');row.append(el('td',names[key]||key),el('td',display(value.before,key)),el('td',display(value.after,key)));body.append(row);shown++;});
      if(shown){table.append(body);const wrap=el('div',undefined,'assistant-table-scroll');wrap.tabIndex=0;wrap.setAttribute('aria-label','操作变更摘要，可横向滚动');box.append(wrap);wrap.append(table);}
      if(keys.length>16)box.append(el('p','摘要最多展示16个字段，请展开核对完整预览。','small muted'));
      box.append(detail('其他预览信息',value));
    }else box.append(fields(value));
    return box;
  }
  function notices(value) {
    const items=[];
    for(const info of [value,value?.coverage]) {
      if(!info||typeof info!=='object')continue;
      if(info.complete===false)items.push('结果覆盖不完整，请核对范围与缺失信息。');
      if(info.has_more===true||info.next)items.push('结果仍有后续页；本次返回不代表全部数据。');
      if(Number.isInteger(info.excluded_schema_records)&&info.excluded_schema_records>0)items.push('有 '+info.excluded_schema_records+' 条记录因字段定义版本不同未纳入。');
      for(const item of [info.warning,...(Array.isArray(info.warnings)?info.warnings:[])])if(typeof item==='string'&&item.trim())items.push(item.length>500?item.slice(0,500)+'…（完整内容见回执）':item);
    }
    return [...new Set(items)];
  }
  function renderWelcome(controls,{ready=true,loading=false}={}) {
    const box=el('div',undefined,'assistant-welcome');box.dataset.placeholder='true';
    box.append(el('span','开始一项工作','assistant-eyebrow'),el('h3',loading?'正在准备助手':ready?'今天需要完成什么？':'助手当前不可用'),el('p','可查询获准数据、整理信息并预览操作。涉及写入时，会请您核对确认。','muted'));
    const prompts=el('div',undefined,'assistant-starters');
    for(const [label,prompt] of [['了解我的权限','请说明我当前具备哪些权限，以及可以进行哪些操作。'],['查阅数据容器','请查询我可以访问的数据容器，说明返回结果的覆盖范围，不要修改数据。']]){const starter=button(label,()=>controls.fillDraft(prompt),'assistant-prompt');starter.disabled=!ready;prompts.append(starter);}
    box.append(prompts,el('p','仅提交获准资料；页面关联只说明讨论位置。','small muted'));return box;
  }
  function renderTurn(turn, controls) {
    const section=el('article',undefined,'assistant-turn');section.dataset.run=turn.id;
    section.setAttribute('aria-label','工作要求与助手回复');
    const user=el('div',turn.prompt,'assistant-user');user.setAttribute('aria-label','您的工作要求');section.append(user);
    if(turn.context_page){const context=el('span',controls.pageName(turn.context_page),'assistant-message-context');context.setAttribute('aria-label','本轮关联页面：'+context.textContent);section.append(context);}
    if(turn.history_hidden){if(active.has(turn.status)&&!turn.cancel_requested)section.append(button('停止任务',()=>controls.stop(turn)));return section;}
    if(turn.detail_truncated){const note=el('div',undefined,'assistant-warning');note.append(el('p','本轮预览或回执较大，会话未完整展示。请先打开完整任务核对。'));const link=el('a','打开完整任务详情');link.href='/console/assistant/'+encodeURIComponent(turn.id);note.append(link);section.append(note);}
    if(turn.pending){
      const pending=turn.pending,card=el('section',undefined,'assistant-preview');card.setAttribute('aria-label','本次操作预览');
      card.append(el('h3',('decision' in pending?'决定已记录：':'需要您确认：')+(labels.operations[pending.operation]||pending.operation)),preview(pending),detail('完整预览与参数',{preview:pending.preview,arguments:pending.arguments}));
      if(!('decision' in pending)&&!turn.cancel_requested&&!turn.detail_truncated){const actions=el('div',undefined,'assistant-card-actions');actions.append(button('拒绝',()=>controls.decide(turn,false)),button('批准本次操作',()=>controls.decide(turn,true),'primary'));card.append(el('p','仅批准当前预览，后续操作仍需分别确认。','small muted'),actions);}
      else card.append(el('p','请核对当前状态与完整内容，等待实际回执。','small muted'));
      section.append(card);
    }
    const result=turn.result;
    if(result){
      if(result.explanation)section.append(api.content.markdown(result.explanation));
      const receipts=result.receipts||[];
      for(const text of [...new Set(receipts.flatMap(receipt=>notices(receipt.result)))])section.append(el('p',text,'assistant-warning'));
      const reads=receipts.filter(receipt=>receipt.status==='read');
      function receiptCard(receipt,index){
        const card=el('section',undefined,'assistant-receipt');
        card.append(el('h4',receipt.status==='denied'?'已拒绝，未执行此操作':labels.operations[receipt.operation]||receipt.operation||'工具回执'));
        if(receipt.result){card.append(fields(receipt.result));const raw=detail('完整回执',receipt.result);raw.dataset.detailKey='receipt-'+index;card.append(raw);}
        return card;
      }
      if(reads.length){const group=el('details',undefined,'assistant-receipt-group');group.dataset.detailKey='read-receipts';group.append(el('summary','工具回执（'+reads.length+'条）'));reads.forEach(receipt=>group.append(receiptCard(receipt,receipts.indexOf(receipt))));section.append(group);}
      // 非只读回执默认直接展示；没有可信摘要时不把真实写入藏到模型结论之后。
      receipts.forEach((receipt,index)=>{if(receipt.status!=='read')section.append(receiptCard(receipt,index));});
      if(!active.has(turn.status)){
        const actions=el('div',undefined,'assistant-message-actions');
        if(result.explanation)actions.append(window.OfficeUI.iconButton('copy','复制回复',()=>api.content.copy(result.explanation)));
        actions.append(window.OfficeUI.iconButton('info','查看任务详情',()=>controls.showDetails(turn)));
        section.append(actions);
      }
      if(receipts.some(receipt=>receipt.status==='received'))section.append(button('刷新业务页面',controls.refreshBusiness,'ui-text-action small'));
    }
    if(active.has(turn.status)){
      const stage=turn.cancel_requested?'已请求停止；已完成操作不会撤回。':turn.pending?('decision' in turn.pending?'决定已记录，等待执行回执':'等待您的确认'):turn.write_inflight?'正在执行已批准操作，结果以回执为准':turn.status==='queued'?'任务已排队，尚未开始':'任务正在处理';
      const row=el('div',undefined,'assistant-stage');row.append(el('span',stage));if(!turn.cancel_requested)row.append(button('停止任务',()=>controls.stop(turn),'ui-text-action small'));section.append(row);
    }
    const outcome=result?.outcome_unknown?'outcome_unknown':turn.status;
    if(outcomes[outcome]&&outcome!=='completed')section.append(el('p',outcomes[outcome],outcome==='cancelled'?'small muted':'assistant-warning'));
    return section;
  }
  api.view={el,button,renderTurn,renderWelcome,active,labels,fields,detail,outcomes,preview};
})();
