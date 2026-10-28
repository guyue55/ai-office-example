/* 页面增强不改变提交参数／版本／权限；不持久化草稿或秘密，不接管原生写入。 */
(() => {
  'use strict';
  const page = document.querySelector('#model-settings');
  if (!page) return;
  const editor = page.querySelector('#model-settings-form');
  const savedForms = Array.from(page.querySelectorAll('[data-saved-action]'));
  const unsaved = page.querySelector('#model-unsaved-note');
  let dirty = false, baseline, originalStatus;
  const status = page.querySelector('#model-edit-status');
  const values = () => JSON.stringify(Array.from(editor.elements)
    .filter(field => field.name && !['hidden','submit','password'].includes(field.type))
    .map(field => [field.name, field.type === 'checkbox' ? field.checked : field.value]));
  function updateState() {
    if (!editor) return;
    // 密钥只判断是否填写，不比较、复制、序列化或存储它的值。
    dirty = values() !== baseline || editor.querySelector('#model-secret').value.length > 0;
    const label = dirty ? '有未保存修改' : originalStatus;
    if (status.textContent !== label) status.textContent = label;
    unsaved.hidden = !dirty;
    const reset = page.querySelector('#model-reset');
    reset.hidden = false; reset.disabled = !dirty;
    savedForms.forEach(form => form.querySelectorAll('button[type=submit]').forEach(button => {
      button.disabled = dirty;
      if (dirty) button.setAttribute('aria-describedby', unsaved.id);
      else button.removeAttribute('aria-describedby');
    }));
  }
  if (editor) {
    const action = editor.querySelector('#model-secret-action');
    const key = editor.querySelector('#model-secret');
    const keyField = editor.querySelector('#model-secret-field');
    const protocol = editor.querySelector('#model-api');
    const auth = editor.querySelector('#model-auth');
    function secretState() {
      const replacing = action.value === 'replace';
      keyField.dataset.modelSecret = action.value;
      key.disabled = !replacing; key.required = replacing; keyField.hidden = !replacing;
      if (!replacing) key.value = '';
      page.querySelector('#model-secret-warning').hidden = action.value !== 'clear';
      action.setAttribute('aria-describedby', action.value === 'clear' ? 'model-secret-help model-secret-warning' : 'model-secret-help');
    }
    function protocolState() {
      const anthropic = protocol.value === 'anthropic-messages';
      const field = editor.querySelector('#model-auth-field');
      field.dataset.modelAuth = anthropic ? 'shown' : 'hidden';
      field.hidden = !anthropic;
      auth.querySelector('[value="x-api-key"]').disabled = !anthropic;
      if (!anthropic) auth.value = 'bearer';
    }
    action.addEventListener('change', secretState);
    protocol.addEventListener('change', protocolState);
    secretState(); protocolState();
    originalStatus = status.textContent; baseline = values();
    window.OfficeForms?.remember(editor);
    // 原生reset的默认动作可能晚于微任务；下一任务再读取复原后的控件。
    editor.addEventListener('reset', () => setTimeout(() => {secretState(); protocolState(); updateState();}, 0));
    editor.addEventListener('input', updateState);
    editor.addEventListener('change', updateState);
    // 原生校验失败时先展开被折叠的高级字段，使错误与焦点可达。
    editor.addEventListener('invalid', event => {
      const details = event.target.closest('details');
      if (details) details.open = true;
    }, true);
    updateState();
  }
  savedForms.forEach(form => {
    // 费用确认是一次性操作选择，不把它当成需要保存的配置草稿。
    form.addEventListener('change', () => window.OfficeForms?.remember(form));
    form.addEventListener('submit', event => {
      if (dirty) {event.preventDefault(); event.stopPropagation(); unsaved.focus();}
    }, true);
  });
  // 只有共享确认流程批准后才进入等待态，取消弹窗不会禁用操作。
  page.addEventListener('office:submit-approved', event => {
    const form = event.target;
    const initiating = form.querySelector('button[type=submit]');
    if (status) status.textContent = initiating?.dataset.pendingLabel || '正在处理…';
    form.setAttribute('aria-busy','true');
    form.inert = true;
    page.querySelectorAll('button[type=submit],button[type=reset]').forEach(button => {
      button.disabled = true;
      if (button.form === form && button.type === 'submit') button.textContent = button.dataset.pendingLabel || '正在处理…';
    });
  });
})();
