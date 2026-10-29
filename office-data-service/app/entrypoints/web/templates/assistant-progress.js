/* 侧栏与工作区共用真实进度；没有公开摘要时不制造“思考过程”。 */
(() => {
  const api=window.OfficeAssistant;if(!api)return;
  const stages={identity:'正在核验身份',model:'等待模型响应',reply:'正在生成回复',tool:'正在执行查询或操作',awaiting_confirmation:'等待您确认',completed:'正在保存结果',failed:'正在记录失败结果',timeout:'正在记录超时结果',budget_exceeded:'正在记录预算结果'};
  const labels=JSON.parse(document.getElementById('assistant-labels').textContent).operations;
  function el(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;}
  function operation(code){const preparing=code.startsWith('prepare:');const key=preparing?code.slice(8):code;return (preparing?'准备预览：':'')+(labels[key]||({describe_operations:'了解可用操作'}[key])||'受控操作');}
  function update(box,turn){
    const value=turn.progress;if(!value)return;
    const active=['queued','running','awaiting_confirmation'].includes(turn.status);
    const completed=value.steps.filter(step=>step.status==='completed').length;
    box.querySelector('[data-progress-stage]').textContent=active?(stages[value.stage]||'任务正在处理'):'处理记录';
    if(box.dataset.seq!==String(value.seq)){box.dataset.seq=String(value.seq);box.dataset.elapsed=String(value.elapsed_ms);box.dataset.seen=String(performance.now());}
    box.dataset.active=String(active);duration(box);
    box.querySelector('[data-progress-count]').textContent=completed+'项已完成'+(value.steps.some(step=>step.status==='running')?' · 执行中':'');
    box.setAttribute('aria-busy',String(active));
    const list=box.querySelector('[data-progress-steps]');
    const existing=new Map(Array.from(list.children).map(node=>[node.dataset.step,node]));
    for(const step of value.steps){
      let node=existing.get(step.id);if(!node){node=el('li');node.dataset.step=step.id;list.append(node);}
      const status={running:'进行中',completed:'完成',failed:'未完成'}[step.status]||'待核对';
      node.dataset.state=step.status;node.textContent=operation(step.operation)+' · '+status+(step.status!=='running'?' · '+(step.elapsed_ms/1000).toFixed(1)+'秒':'');
    }
    const text=box.querySelector('[data-progress-text]');
    if(active&&value.text){text.hidden=false;if(text.textContent!==value.text)text.textContent=value.text;}
    else{text.hidden=true;text.textContent='';}
    box.querySelector('[data-progress-truncated]').hidden=!value.truncated;
  }
  function render(turn){
    const box=el('section',undefined,'assistant-progress');box.dataset.progress='';box.setAttribute('aria-label','执行过程');
    const details=el('details'),summary=el('summary'),title=el('span'),time=el('span',undefined,'muted small'),count=el('span',undefined,'muted small');
    details.dataset.detailKey='progress';title.dataset.progressStage='';time.dataset.progressDuration='';count.dataset.progressCount='';summary.append(title,time,count);
    const list=el('ol');list.dataset.progressSteps='';details.append(summary,list);
    const text=el('div',undefined,'assistant-stream-text');text.dataset.progressText='';text.setAttribute('aria-label','正在生成的回复草稿');
    const hint=el('p','过程展示已达上限，请以最终结果及完整回执为准。','small muted');hint.dataset.progressTruncated='';hint.hidden=true;
    box.append(details,text,hint);update(box,turn);return box;
  }
  function duration(box){const elapsed=Number(box.dataset.elapsed||0)+(box.dataset.active==='true'?performance.now()-Number(box.dataset.seen||performance.now()):0);box.querySelector('[data-progress-duration]').textContent=(elapsed/1000).toFixed(1)+'秒';}
  let clock=null;
  function startClock(){if(clock===null)clock=setInterval(()=>{if(!api.root.hidden&&!document.hidden)api.root.querySelectorAll('[data-progress][data-active="true"]').forEach(duration);},1000);}
  startClock();window.addEventListener('pageshow',startClock);
  window.addEventListener('pagehide',()=>{clearInterval(clock);clock=null;});
  api.progress={render,update,stages};
})();
