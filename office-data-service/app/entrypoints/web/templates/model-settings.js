/* 当前输入可发现／测试；不持久化秘密，不自动保存，不重放收费请求。 */
(() => {
  'use strict';
  const page = document.querySelector('#model-settings');
  const editor = page?.querySelector('#model-settings-form');
  if (!editor) return;
  const field = id => editor.querySelector('#model-' + id);
  const action = field('secret-action'), key = field('secret'), mode = field('auth-mode');
  const status = page.querySelector('#model-edit-status'), output = field('inline-result');
  const savedForms = Array.from(page.querySelectorAll('[data-saved-action]'));
  const picker = window.OfficeCombobox.create(field('name'), field('options'));
  let baseline, busy = false, generation = 0, catalogGeneration = 0, controller;
  const values = () => JSON.stringify(Array.from(editor.elements).filter(el => el.name && !el.hasAttribute('data-sensitive') && !['hidden','submit','password'].includes(el.type)).map(el => [el.name, el.type === 'checkbox' ? el.checked : el.value]));
  function secretState() {
    const anonymous = mode.value === 'none', replacing = !anonymous && action.value === 'replace';
    action.disabled = anonymous;
    field('auth-header').closest('.settings-field').hidden = mode.value !== 'custom-header'; key.disabled = !replacing; key.required = replacing;
    field('secret-field').hidden = anonymous;
    if (!replacing) {key.value = ''; key.type = 'password';}
    field('secret-replace').hidden = anonymous || replacing;
    field('secret-clear').hidden = anonymous || action.value === 'clear';
    field('secret-reveal').hidden = !replacing;
    field('key-label').textContent = anonymous ? '无需密钥' : replacing ? '填写新密钥' : action.value === 'keep' && page.dataset.hasSecret === 'true' ? '已配置' : '未配置';
    field('secret-warning').hidden = action.value !== 'clear';
    const changed = page.dataset.hasSecret === 'true' && action.value === 'keep' && !anonymous && field('url').value.replace(/\/$/,'') !== page.dataset.savedUrl;
    field('key-consent').hidden = !changed;
  }
  function protocolState() {
    const anthropic = field('api').value === 'anthropic-messages';
    field('auth-field').hidden = !anthropic;
    field('auth-field').dataset.modelAuth = anthropic ? 'shown' : 'hidden';
    if (!anthropic) field('auth').value = 'bearer';
    field('auth').querySelector('[value="x-api-key"]').disabled = !anthropic;
  }
  function update(event) {
    generation++;
    if (event?.target !== field('name') && event?.target?.name !== 'approved_model_data') {catalogGeneration++; picker.setItems([]);}
    if (output.textContent) {output.textContent = '参数已变更，请重新获取模型或测试连接。'; output.dataset.state = 'idle';}
    secretState(); protocolState();
    const dirty = values() !== baseline || key.value.length > 0;
    status.textContent = dirty ? '有未保存修改' : page.dataset.savedUrl ? '已保存' : '尚未保存';
    field('reset').hidden = false; field('reset').disabled = !dirty;
    const note = page.querySelector('#model-unsaved-note'); if (note) note.hidden = !dirty;
    savedForms.forEach(form => form.querySelectorAll('button[type=submit]').forEach(button => button.disabled = dirty || busy));
  }
  function result(message, state='idle') {output.textContent = message; output.dataset.state = state;}
  function payload(kind) {
    const data = new FormData(editor);
    const connection = {api:data.get('api'), model_url:String(data.get('model_url') || ''), auth_mode:data.get('auth_mode'), anthropic_auth:data.get('anthropic_auth'), auth_header:data.get('auth_header'), extra_headers:JSON.parse(String(data.get('extra_headers') || '{}')), extra_query:JSON.parse(String(data.get('extra_query') || '{}')), stream_output:data.get('stream_output')!=='false', request_timeout:Number(data.get('request_timeout'))};
    const body = {version:Number(data.get('version')), secret_action:data.get('secret_action') || 'keep', reuse_secret_at_new_url:data.get('reuse_secret_at_new_url') === 'on'};
    if (data.get('secret')) body.secret = data.get('secret');
    if (kind === 'models') body.connection = connection;
    else {
      const limits = {}; editor.querySelectorAll('[name^=limit_]').forEach(el => limits[el.name.slice(6)] = Number(el.value));
      body.draft = {...connection, model:String(data.get('model') || ''), limits, approved_model_data:data.get('approved_model_data') === 'on'};
      body.acknowledge_cost = true;
      // 每次明确发起一个新标识；同一次网络请求失败不自动重试。LAN HTTP无需randomUUID。
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      body.request_key = Array.from(bytes, byte => byte.toString(16).padStart(2,'0')).join('');
    }
    return {csrf:data.get('csrf'), action:kind, payload:body};
  }
  async function inspect(kind) {
    if (busy) return;
    for (const el of [field('url'), ...(kind === 'probe' ? [field('name')] : []), key]) {
      if (!el.disabled && !el.reportValidity()) return;
    }
    if (!field('key-consent').hidden && !editor.elements.reuse_secret_at_new_url.checked) {result('服务地址已改变，请确认沿用原密钥或填写新密钥。','error'); return;}
    if (kind === 'probe' && !await window.OfficeUI.confirm('使用当前输入发送一次短合成提示，可能产生费用。不会保存或启用配置。', {title:'测试模型连接', accept:'确认测试'})) return;
    let body;
    try {body = payload(kind);} catch {result('高级网关参数须为有效JSON对象。','error'); return;}
    const scope = generation, catalogScope = catalogGeneration;
    busy = true; controller = new AbortController();
    const timeout = setTimeout(() => {controller.abort(); if (scope === generation) result('等待回执超时，调用可能已发生。请核对最近测试记录，不要直接重复测试。','error');}, kind === 'models' ? 25000 : 45000);
    const trigger = field(kind === 'models' ? 'refresh' : 'probe');
    trigger.setAttribute('aria-busy','true');
    editor.querySelectorAll('button[type=submit]').forEach(button => button.disabled = true);
    result(kind === 'models' ? '正在获取模型列表…' : '正在测试当前模型…', 'loading');
    try {
      const response = await fetch('/console/model-settings/inspect', {method:'POST', headers:{'Content-Type':'application/json','Accept':'application/json'}, credentials:'same-origin', body:JSON.stringify(body), signal:controller.signal});
      const envelope = await response.json();
      if (!response.ok || envelope.code !== 0) {throw new Error(envelope.message ? `${envelope.message}${envelope.meta?.trace_id ? ' · 追踪号：' + envelope.meta.trace_id : ''}` : '操作未完成，请核对登录与权限。');}
      const value = envelope.data;
      if (kind === 'models' ? catalogScope !== catalogGeneration : scope !== generation) return;
      if (kind === 'models' && value.items.length) picker.setItems(value.items);
      result(value.passed ? kind === 'models' ? `已获取 ${value.items.length} 个模型${value.truncated ? '（列表不完整，可搜索已有结果或直接输入ID）' : ''}，点击模型框选择。` : `当前输入的模型调用通过 · ${value.elapsed_ms}ms；尚未保存或启用。` : `${value.message}。${value.suggestion}`, value.passed ? 'success' : 'error');
    } catch (error) {
      if (!controller.signal.aborted && scope === generation) result(error instanceof TypeError || error instanceof SyntaxError ? '无法读取服务回执，请检查网络与登录状态。测试可能已发生，请先核对最近记录。' : error instanceof Error ? error.message : '操作未完成。', 'error');
    } finally {
      clearTimeout(timeout); busy = false; trigger.removeAttribute('aria-busy');
      editor.querySelectorAll('button[type=submit]').forEach(button => button.disabled = false);
      const dirty = values() !== baseline || key.value.length > 0;
      savedForms.forEach(form => form.querySelectorAll('button[type=submit]').forEach(button => button.disabled = dirty));
    }
  }
  editor.addEventListener('submit', event => {
    const target = event.submitter?.getAttribute('formaction') || '';
    if (target.endsWith('/models') || target.endsWith('/probe')) {
      event.preventDefault(); event.stopImmediatePropagation();
      void inspect(target.endsWith('/models') ? 'models' : 'probe');
    }
  }, true);
  field('probe')?.addEventListener('click', event => {event.preventDefault(); event.stopImmediatePropagation(); void inspect('probe');}, true);
  field('refresh')?.addEventListener('click', event => {event.preventDefault(); event.stopImmediatePropagation(); void inspect('models');}, true);
  field('secret-replace').addEventListener('click', () => {action.value = 'replace'; update(); key.focus();});
  field('secret-clear').addEventListener('click', () => {action.value = 'clear'; update();});
  field('secret-reveal').addEventListener('click', event => {key.type = key.type === 'password' ? 'text' : 'password'; event.currentTarget.setAttribute('aria-label', key.type === 'password' ? '显示新输入的密钥' : '隐藏新输入的密钥');});
  editor.addEventListener('input', update); editor.addEventListener('change', update);
  editor.addEventListener('reset', () => setTimeout(() => {picker.setItems([]); update();}, 0));
  editor.addEventListener('invalid', event => {const details = event.target.closest('details'); if (details) details.open = true;}, true);
  savedForms.forEach(form => form.addEventListener('submit', event => {if (values() !== baseline || key.value) {event.preventDefault(); event.stopImmediatePropagation(); page.querySelector('#model-unsaved-note')?.focus();}}, true));
  page.addEventListener('office:submit-approved', event => {
    const form = event.target; form.setAttribute('aria-busy','true'); form.inert = true;
    page.querySelectorAll('button[type=submit],button[type=reset]').forEach(button => {button.disabled = true;});
    const button = event.detail?.submitter; if (button) button.textContent = button.dataset.pendingLabel || '正在处理…';
  });
  window.addEventListener('pagehide', () => controller?.abort());
  secretState(); protocolState(); baseline = values(); window.OfficeForms?.remember(editor);
})();
