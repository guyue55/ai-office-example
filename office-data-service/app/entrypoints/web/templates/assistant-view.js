/* 同一渲染器服务侧栏与工作区；用户文本保持原文；模型说明经受限Markdown渲染。 */
(() => {
  const api = window.OfficeAssistant;
  if (!api) return;
  const labels = JSON.parse(document.getElementById('assistant-labels').textContent);
  const active = new Set(['queued','running','awaiting_confirmation']);
  const names = {name:'名称', id:'编号', version:'版本', before:'变更前', after:'变更后', values:'字段值', operation:'操作', status:'状态', schema_revision:'字段定义版本', project_id:'数据容器编号', complete:'覆盖完整', next:'下一页', count:'数量', total:'总数', archived:'已归档', permissions:'权限', items:'数据项', warning:'提示', warnings:'提示', message:'说明', active:'启用'};
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
  function renderTurn(turn, controls) {
    const section=el('article',undefined,'assistant-turn'); section.dataset.run=turn.id;
    const head=el('div',undefined,'assistant-turn-head');
    head.append(el('span','您 · '+(turn.context_page ? controls.pageName(turn.context_page) : '未关联页面')),el('span', labels.statuses[turn.status] || '状态待核对'));
    section.append(head,el('div',turn.prompt,'assistant-user'));
    if (turn.history_hidden) {
      if(active.has(turn.status)&&!turn.cancel_requested)section.append(button('停止任务',()=>controls.stop(turn)));
      return section;
    }
    if(turn.detail_truncated){
      const note=el('div',undefined,'assistant-preview');
      note.append(el('p','这轮预览或回执较大，未在会话中展开；请到独立详情核对完整内容。','small muted'));
      const link=el('a','打开完整任务详情');link.href='/console/assistant/'+encodeURIComponent(turn.id);note.append(link);section.append(note);
    }
    if (turn.pending) {
      const pending=turn.pending; const card=el('section',undefined,'assistant-preview');
      card.append(el('h3','需要您确认：'+(labels.operations[pending.operation] || pending.operation)), preview(pending), detail('完整预览与参数',{preview:pending.preview,arguments:pending.arguments}));
      if (!('decision' in pending) && !turn.cancel_requested) {
        const actions=el('div',undefined,'assistant-card-actions');
        actions.append(button('拒绝',()=>controls.decide(turn,false)),button('批准本次操作',()=>controls.decide(turn,true),'primary'));
        card.append(el('p','批准仅适用于当前预览；后续操作仍需分别确认。','small muted'),actions);
      } else card.append(el('p','决定或停止请求已记录，等待实际回执。','small muted'));
      section.append(card);
    }
    if (turn.result) {
      if (turn.result.explanation) {section.append(api.content.markdown(turn.result.explanation));section.append(button('复制回复',()=>api.content.copy(turn.result.explanation),'secondary small'));}
      if(turn.result.usage){const usage=turn.result.usage;section.append(el('p','模型请求 '+(usage.requests??0)+' 次 · 输入 '+(usage.input_tokens??0)+' / 输出 '+(usage.output_tokens??0)+' tokens','small muted'));}
      (turn.result.receipts || []).forEach(receipt => {
        const card=el('section',undefined,'assistant-preview');
        card.append(el('h3',receipt.status==='denied' ? '已拒绝，未执行此操作' : '实际工具回执'),el('p',labels.operations[receipt.operation] || receipt.operation || '数据查询','small muted'));
        if (receipt.result) card.append(fields(receipt.result),detail('查看完整回执',receipt.result));
        section.append(card);
      });
      if((turn.result.receipts||[]).some(receipt=>receipt.status==='received')) {
        const refresh=el('div',undefined,'assistant-card-actions');
        refresh.append(el('span','如操作影响当前页面，请先保存表单，再刷新核对。','small muted'),button('刷新业务页面',controls.refreshBusiness));
        section.append(refresh);
      }
      if (turn.result.outcome_unknown || turn.status==='outcome_unknown') section.append(el('p','写入结果待核实，请先检查实际数据，不要重复提交。','hint'));
    }
    if (active.has(turn.status)) {
      const stage=turn.cancel_requested?'已请求停止，等待执行结束':turn.pending?('decision' in turn.pending?'决定已记录，等待执行回执':'等待您的确认'):turn.write_inflight?'正在执行已批准操作，结果以回执为准':turn.status==='queued'?'任务已排队，尚未开始':'任务处理中，尚无新的执行回执';
      section.append(el('p',stage,'assistant-stage small muted'));
      const actions=el('div',undefined,'assistant-card-actions');
      if (!turn.cancel_requested) actions.append(button('停止任务',()=>controls.stop(turn)));
      else actions.append(el('span','已请求停止；已完成的操作不会撤回。','small muted'));
      section.append(actions);
    }
    if(!turn.result&&turn.status==='outcome_unknown')section.append(el('p','写入结果待核实，请先检查实际数据，不要重复提交。','hint'));
    if (turn.status==='interrupted') section.append(el('p','任务已中断，未自动恢复；请核对实际回执后再发起新任务。','hint'));
    return section;
  }
  api.view={el,button,renderTurn,active};
})();
