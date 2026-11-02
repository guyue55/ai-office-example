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
      const choices = Array.from(form.elements).filter(el => el.type === 'checkbox');
      const initial = new Map(choices.map(el => [el, el.checked]));
      const status = form.querySelector('#grant-status');
      const updateSummary = () => {
        const added = choices.filter(el => el.checked && !initial.get(el));
        const removed = choices.filter(el => !el.checked && initial.get(el));
        const count = added.length + removed.length;
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
    });
  }
  window.OfficeForms = {setup, remember, dirty, captureDirty};
})();
