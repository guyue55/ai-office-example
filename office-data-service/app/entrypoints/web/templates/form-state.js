(() => {
  'use strict';
  const snapshots = new WeakMap();
  const formValue = form => Array.from(form.elements)
    .filter(el => el.name && !el.hasAttribute('data-sensitive') && !['hidden', 'submit', 'password'].includes(el.type))
    .map(el => [el.name, ['checkbox', 'radio'].includes(el.type) ? el.checked : el.value]);
  const remember = form => snapshots.set(form, JSON.stringify(formValue(form)));
  const dirtyForm = form => snapshots.has(form) &&
    snapshots.get(form) !== JSON.stringify(formValue(form));
  // 仅比较已明确允许放弃的那一版草稿；请求期间继续编辑会再次被识别。
  const captureDirty = (root, ignoredForm = null) => new Map(Array.from(root.querySelectorAll('form'))
    .filter(form => form !== ignoredForm && dirtyForm(form)).map(form => [form, JSON.stringify(formValue(form))]));
  const dirty = (root, ignoredForm = null, approved = new Map()) => Array.from(root.querySelectorAll('form'))
    .some(form => form !== ignoredForm && dirtyForm(form) && approved.get(form) !== JSON.stringify(formValue(form)));
  const initialized = new WeakSet();
  function setup(root) {
    // 所有可编辑写入表单都保护草稿；GET筛选、隐藏字段与密码值不进入快照。
    root.querySelectorAll('form').forEach(form => {
      if (form.method.toLowerCase() === 'post' && !snapshots.has(form)) remember(form);
    });
    root.querySelectorAll('[data-grant-editor]').forEach(form => {
      if (initialized.has(form)) return;
      initialized.add(form);
      remember(form);
      const choices = Array.from(form.elements).filter(el => el.type === 'checkbox' && el.name.startsWith('id_'));
      const initial = new Map(choices.map(el => [el, el.checked]));
      const status = form.querySelector('#grant-status');
      const selectPage=form.querySelector('[data-select-page]');
      const pageChoices=choices.filter(choice=>form.querySelector('tbody')?.contains(choice)&&!choice.disabled);
      const selectionCount=form.querySelector('[data-selection-count]');
      const reset=form.querySelector('[data-grant-reset]');
      if(selectPage){selectPage.closest('label').hidden=false;selectPage.disabled=!pageChoices.length;}
      if(reset)reset.hidden=false;
      const updateSummary = () => {
        const added = choices.filter(el => el.checked && !initial.get(el));
        const removed = choices.filter(el => !el.checked && initial.get(el));
        const count = added.length + removed.length;
        const pageSelected=pageChoices.filter(choice=>choice.checked).length;
        if(selectPage){selectPage.checked=pageChoices.length>0&&pageSelected===pageChoices.length;selectPage.indeterminate=pageSelected>0&&pageSelected<pageChoices.length;}
        if(selectionCount)selectionCount.textContent='本页已选 '+pageSelected+' / '+pageChoices.length+' 项；其他页或筛选外已选 '+choices.filter(choice=>!pageChoices.includes(choice)&&choice.checked).length+' 项。';
        if(reset)reset.disabled=count===0;
        const label = el => {
          const owner = el.closest('tr') || el.closest('label');
          return owner?.querySelector('strong')?.textContent.trim() ||
            owner?.textContent.trim() || el.value;
        };
        status.textContent = count
          ? `有 ${count} 项未保存的修改。新增 ${added.length} 项，移除 ${removed.length} 项。` +
            [...added.map(el => '新增：' + label(el)), ...removed.map(el => '移除：' + label(el))].join('；')
          : '当前没有未保存的修改。';
      };
      choices.forEach(choice => choice.addEventListener('change', updateSummary));
      selectPage?.addEventListener('change',()=>{
        pageChoices.forEach(choice=>{choice.checked=selectPage.checked;});
        updateSummary();
      });
      // reset默认动作完成后再同步，不能在reset事件或微任务中抢先读取旧状态。
      form.addEventListener('reset',()=>setTimeout(updateSummary,0));
      updateSummary();
    });
  }
  window.OfficeForms = {setup, remember, dirty, captureDirty};
})();
