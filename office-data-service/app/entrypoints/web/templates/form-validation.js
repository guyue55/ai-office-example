/* 只增强原生约束反馈；不替代服务端校验，不保存输入，也不接管提交。 */
(() => {
  const descriptions=new WeakMap(), summaries=new WeakMap(), pending=new WeakSet();
  let sequence=0;
  const fields=form=>Array.from(form.elements).filter(field=>field.willValidate);
  function label(field) {
    if(field.dataset.validationLabel)return field.dataset.validationLabel;
    // 包裹式label也包含输入、选项和动态错误；名称只取原说明，避免反馈递归增长。
    const copy=field.labels?.[0]?.cloneNode(true);
    copy?.querySelectorAll('input,select,textarea,.field-error').forEach(node=>node.remove());
    return copy?.textContent.trim()||field.getAttribute('aria-label')||'此项';
  }
  function message(field) {
    const name=label(field),validity=field.validity;
    if(validity.valueMissing)return '请填写'+name+'。';
    if(validity.typeMismatch)return field.type==='email'?'请输入有效的邮箱地址。':'请检查'+name+'的格式。';
    if(validity.patternMismatch)return '请按填写说明检查'+name+'。';
    if(validity.tooShort)return name+'至少需要'+field.minLength+'个字符。';
    if(validity.tooLong)return name+'最多允许'+field.maxLength+'个字符。';
    if(validity.rangeUnderflow)return name+'不能小于'+field.min+'。';
    if(validity.rangeOverflow)return name+'不能大于'+field.max+'。';
    return '请检查'+name+'，填写有效的值。';
  }
  function clear(field) {
    const saved=descriptions.get(field);if(!saved)return;
    saved.node.remove();
    if(saved.described===null)field.removeAttribute('aria-describedby');else field.setAttribute('aria-describedby',saved.described);
    if(saved.invalid===null)field.removeAttribute('aria-invalid');else field.setAttribute('aria-invalid',saved.invalid);
    descriptions.delete(field);
  }
  function render(form,{focus=false}={}) {
    let summary=summaries.get(form);
    const invalid=fields(form).filter(field=>!field.validity.valid);
    fields(form).filter(field=>field.validity.valid).forEach(clear);
    if(!invalid.length){summary?.remove();summaries.delete(form);return;}
    if(!summary){summary=document.createElement('section');summary.className='form-errors';summary.tabIndex=-1;summary.setAttribute('aria-label','请检查填写内容');form.prepend(summary);summaries.set(form,summary);}
    const heading=document.createElement('strong');heading.textContent='有'+invalid.length+'项需要检查';
    const list=document.createElement('ul');
    invalid.forEach(field=>{
      let saved=descriptions.get(field);
      if(!saved){const node=document.createElement('span');node.id='field-error-'+(++sequence);node.className='field-error';saved={node,described:field.getAttribute('aria-describedby'),invalid:field.getAttribute('aria-invalid')};descriptions.set(field,saved);field.insertAdjacentElement('afterend',node);field.setAttribute('aria-describedby',[saved.described,node.id].filter(Boolean).join(' '));}
      saved.node.textContent=message(field);field.setAttribute('aria-invalid','true');
      const item=document.createElement('li'),link=document.createElement('button');link.type='button';link.className='field-error-link';link.textContent=message(field);link.addEventListener('click',()=>field.focus());item.append(link);list.append(item);
    });
    summary.replaceChildren(heading,list);if(focus)summary.focus();
  }
  document.addEventListener('invalid',event=>{
    const form=event.target.form;if(!form||form.method!=='post'||form.noValidate)return;
    event.preventDefault();if(pending.has(form))return;pending.add(form);
    queueMicrotask(()=>{pending.delete(form);if(form.isConnected)render(form,{focus:true});});
  },true);
  document.addEventListener('input',event=>{const form=event.target.form;if(form&&summaries.has(form))render(form);});
  document.addEventListener('change',event=>{const form=event.target.form;if(form&&summaries.has(form))render(form);});
  document.addEventListener('reset',event=>{fields(event.target).forEach(clear);summaries.get(event.target)?.remove();summaries.delete(event.target);});
})();
