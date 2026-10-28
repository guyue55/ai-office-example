(() => {
  'use strict';
  const panel = document.getElementById('workspace');
  if (!panel || !window.HTMLDialogElement || !panel.showModal) return;
  const {setup, remember, dirty, captureDirty} = window.OfficeForms;
  const content = panel.querySelector('#workspace-content');
  const feedback = panel.querySelector('#workspace-feedback');
  const title = panel.querySelector('#workspace-title');
  const full = panel.querySelector('[data-workspace-full]');
  const back = panel.querySelector('[data-workspace-back]');
  const reload = panel.querySelector('[data-workspace-reload]');
  let opener, local, currentURL, trail = [], busy = false, writing = false, changed = false, leaving = false;
  let controller, savedMessage, savedDrafts;

  const draftInTrail = () => trail.some(view => view.nodes.some(node => node.nodeType === 1 && dirty(node)));
  const mayLeave = async (root, ignoredForm = null) => (!dirty(root, ignoredForm) && !(root === content && draftInTrail())) ||
    window.OfficeUI.confirm('修改尚未保存。离开会丢失修改，确定继续吗？', {title:'放弃未保存的修改？', accept:'放弃修改并继续', danger:true});
  const allowed = url => url.origin === location.origin && (
    url.pathname.startsWith('/console/access/') || url.pathname.startsWith('/console/links/') ||
    url.pathname === '/console/accounts/new' || url.pathname === '/console/accounts/link' || url.pathname.startsWith('/console/accounts/employees/')
  );
  const showFeedback = (message, error = false, loading = false) => {
    feedback.toggleAttribute('data-loading', loading);
    feedback.textContent = message;
    feedback.className = error ? 'workspace-feedback error-feedback' : 'workspace-feedback';
  };
  setup(document);

  function openPanel(trigger, compact = false) {
    opener = trigger;
    panel.classList.toggle('is-compact', compact);
    panel.showModal();
    document.body.classList.add('workspace-open');
    title.focus();
  }
  function parsePage(html, {preserveShell = false} = {}) {
    const documentCopy = new DOMParser().parseFromString(html, 'text/html');
    const main = documentCopy.querySelector('main');
    if (!main) throw new Error('页面内容不可用，请在完整页面中核对。');
    // 仅导入服务端页面内容，不执行响应里的脚本或外部资源声明。
    main.querySelectorAll('script, style, link').forEach(node => node.remove());
    if (!preserveShell) main.querySelectorAll('.assistant-launchbar, .shell-context').forEach(node => node.remove());
    return main;
  }
  function paint(main, url) {
    currentURL = url;
    full.href = url;
    full.hidden = false;
    reload.hidden = new URL(url).pathname === '/console/accounts/new';
    title.textContent = main.querySelector('[data-panel-title]')?.dataset.panelTitle || main.querySelector('h2')?.textContent.trim() || '管理操作';
    // 服务端页面中的 ?q / ?after 相对链接必须以抽屉页面为基准，
    // 不能错误解析成背后人员列表的 URL。
    main.querySelectorAll('a[href]').forEach(link => {
      link.href = new URL(link.getAttribute('href'), url).href;
    });
    content.replaceChildren(...Array.from(main.childNodes));
    content.querySelector('.page-header h2')?.setAttribute('class', 'workspace-section-title');
    panel.classList.toggle('is-compact', new URL(url).pathname === '/console/accounts/new');
    back.hidden = !trail.length;
    setup(content);
    content.scrollTop = 0;
    title.focus();
  }
  async function load(url, push = false) {
    if (busy) return;
    busy = true;
    content.inert = true;
    content.setAttribute('aria-busy', 'true');
    reload.disabled = true; back.disabled = true;
    controller = new AbortController();
    const requestController = controller;
    const deadline = setTimeout(() => requestController.abort(), 15000);
    showFeedback('正在加载，请稍候…', false, true);
    try {
      const response = await fetch(url, {
        credentials: 'same-origin', signal: requestController.signal,
        headers: {'Accept': 'text/html'}
      });
      const finalURL = new URL(response.url);
      if (!allowed(finalURL)) {
        throw new Error('登录状态已变化，请关闭面板并重新登录。');
      }
      const html = await response.text();
      // 关闭或新请求已接管时，不允许旧响应绘制新面板。
      if (controller !== requestController || !panel.open) return;
      const main = parsePage(html);
      if (!response.ok) {
        const detail = main.querySelector('[role=alert],.error-feedback')?.textContent.trim();
        const message = response.status === 403 ? '无权访问此内容，请核对当前权限。' : '页面暂时无法加载（HTTP ' + response.status + '），请刷新配置重试。';
        throw new Error(detail ? detail.slice(0, 500) : message);
      }
      if (push && currentURL) trail.push({url: currentURL, nodes: Array.from(content.childNodes)});
      paint(main, finalURL.href);
      showFeedback('');
    } catch (error) {
      if (controller === requestController && panel.open) {
        if (!content.hasChildNodes()) title.textContent = '加载未完成';
        reload.hidden = false;
        showFeedback(error.name === 'AbortError' ? '加载已取消或超时，可刷新配置重试。' : error.message || '加载失败，请稍后重试。', true);
      }
    } finally {
      clearTimeout(deadline);
      if (controller === requestController) {
        controller = null; busy = false;
        content.inert = false; content.removeAttribute('aria-busy');
        reload.disabled = false; back.disabled = false;
      }
    }
  }
  async function refreshList(message, approvedDrafts = new Map()) {
    const focusHref = opener?.getAttribute('href');
    const root = document.querySelector('main');
    const originURL = location.href;
    // 请求期间新产生的草稿仍属于员工，不能因另一项操作已成功而覆盖。
    if (dirty(root, null, approvedDrafts)) {
      window.OfficeUI.notify((message || '操作已提交。') + ' 当前页面有未保存的修改，列表未自动刷新；请保存草稿后刷新核对。');
      return;
    }
    const refreshController = new AbortController();
    const refreshDeadline = setTimeout(() => refreshController.abort(), 15000);
    // 保存后刷新期间阻止在旧DOM中继续输入，避免响应覆盖刚输入的搜索或编辑。
    const controls = Array.from(root.querySelectorAll('input,select,textarea,button')).map(el => [el, el.disabled]);
    root.inert = true; root.setAttribute('aria-busy', 'true');
    controls.forEach(([el]) => el.disabled = true);
    try {
      const response = await fetch(originURL, {credentials: 'same-origin', signal: refreshController.signal});
      if (!response.ok || new URL(response.url).pathname !== location.pathname) {
        throw new Error('列表状态更新失败，请刷新核对最新配置。');
      }
      const main = parsePage(await response.text(), {preserveShell: true});
      if (location.href !== originURL || leaving) return;
      const scroll = window.scrollY;
      // 保留现有节点及其监听器；抽屉裁剪不能用于替换整个页面外壳。
      for (const selector of ['.assistant-launchbar', '.shell-context']) {
        const original = root.querySelector(selector), incoming = main.querySelector(selector);
        if (original && incoming) incoming.replaceWith(original);
      }
      document.querySelector('main').replaceChildren(...Array.from(main.childNodes));
      setup(document.querySelector('main'));
      root.inert = false;
      if (message) {
        const notice = document.createElement('p');
        notice.className = 'notice'; notice.setAttribute('role', 'status');
        notice.textContent = message;
        document.querySelector('main').prepend(notice);
      }
      window.scrollTo(0, scroll);
      const target = focusHref && Array.from(document.querySelectorAll('main a[href]'))
        .find(link => link.getAttribute('href') === focusHref);
      window.OfficeUI?.enhance(document);
      if (!panel.open && !window.OfficeUI?.restoreFocus()) {
        const restored = target || document.querySelector('main h2');
        if (restored) { restored.tabIndex = restored.tabIndex < 0 ? -1 : restored.tabIndex; restored.focus({preventScroll: true}); }
      }
    } catch {
      const notice = document.createElement('p');
      notice.className = 'notice error-feedback';
      notice.textContent = '操作已提交，但列表刷新失败。请刷新页面核对最新配置。';
      document.querySelector('main').prepend(notice);
    } finally {
      clearTimeout(refreshDeadline);
      root.inert = false; root.removeAttribute('aria-busy');
      controls.forEach(([el, disabled]) => { if (el.isConnected) el.disabled = disabled; });
    }
  }
  async function closePanel() {
    if (writing || !await mayLeave(content)) return;
    if (local) local.holder.querySelectorAll('form').forEach(form => form.reset());
    controller?.abort();
    panel.close();
  }
  panel.querySelector('[data-workspace-close]').addEventListener('click', closePanel);
  panel.addEventListener('cancel', event => { event.preventDefault(); closePanel(); });
  // 不通过点击遮罩关闭编辑面板，避免误触丢失草稿。
  panel.addEventListener('close', () => {
    controller?.abort(); controller = null; busy = false;
    content.inert = false; content.removeAttribute('aria-busy');
    reload.disabled = false; back.disabled = false;
    if (local) {
      local.source.append(...Array.from(local.holder.childNodes));
      local.source.open = false;
      local = null;
    }
    content.replaceChildren();
    document.body.classList.remove('workspace-open');
    opener?.focus({preventScroll: true});
    if (changed) refreshList(savedMessage, savedDrafts);
    savedMessage = null; savedDrafts = undefined;
    changed = false; currentURL = null; trail = [];
    showFeedback('');
  });
  back.addEventListener('click', async () => {
    if (busy || !trail.length || (dirty(content) && !await window.OfficeUI.confirm('当前修改尚未保存，确定放弃修改并返回吗？', {title:'返回上一步？', accept:'放弃修改并返回', danger:true}))) return;
    const view = trail.pop();
    content.replaceChildren(...view.nodes);
    currentURL = view.url; full.href = view.url;
    title.textContent = content.querySelector('[data-panel-title]')?.dataset.panelTitle || content.querySelector('h2')?.textContent.trim() || '管理操作';
    back.hidden = !trail.length;
    showFeedback('');
    title.focus();
  });
  reload.addEventListener('click', async () => {
    if (!busy && currentURL && await mayLeave(content)) load(currentURL);
  });
  const confirmAction = (form, submitter) => window.OfficeUI.confirm(
    submitter?.dataset.confirmMessage ||
    form.querySelector('select[name="action"] option:checked')?.dataset.confirmMessage || form.dataset.confirmMessage
  );
  // 只有真正离开文档才确认助手草稿；抽屉fetch与异步保存不触发离页。
  const mayNavigate = () => window.OfficeUI.confirmNavigation?.() ?? true;
  const pendingWrite = () => {
    if (!writing) return false;
    window.OfficeUI.notify('操作正在提交，请等待回执后再切换页面。', true);
    return true;
  };
  // 助手刷新也使用同一离页许可，避免业务草稿再次触发原生警告。
  window.OfficeUI.reloadPage = async () => {
    if (pendingWrite()) return;
    if (dirty(document.querySelector('main')) && !await window.OfficeUI.confirm('当前页面有未保存的修改。刷新会丢失这些内容，是否继续？', {title:'刷新业务页面？', accept:'放弃修改并刷新'})) return;
    if (!await mayNavigate()) return;
    leaving = true;
    location.reload();
  };

  document.addEventListener('click', async event => {
    const formTrigger = event.target.closest('[data-open-form]');
    const summary = formTrigger
      ? document.getElementById(formTrigger.dataset.openForm)?.querySelector('summary')
      : event.target.closest('[data-form-dialog] > summary');
    if (summary && !panel.open) {
      event.preventDefault();
      if (pendingWrite()) return;
      if (!await mayLeave(document.querySelector('main'))) return;
      const source = summary.parentElement;
      const holder = document.createElement('div');
      Array.from(source.childNodes).filter(node => node !== summary).forEach(node => holder.append(node));
      local = {source, holder};
      content.replaceChildren(holder);
      holder.querySelectorAll('form').forEach(remember);
      title.textContent = summary.textContent.trim();
      full.hidden = true; back.hidden = true; reload.hidden = true;
      openPanel(formTrigger || summary, true);
      holder.querySelector('input:not([type=hidden])')?.focus();
      return;
    }
    const link = event.target.closest('a[href]');
    if (!link || link.target === '_blank' || event.button !== 0 ||
        event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (panel.open && content.contains(link) && link.hasAttribute('data-workspace-dismiss')) { event.preventDefault(); closePanel(); return; }
    const url = new URL(link.href);
    // 当前文档锚点只是定位，不离页，不应丢弃或确认助手草稿。
    if (!panel.open && url.origin === location.origin && url.pathname === location.pathname && url.search === location.search && url.hash) return;
    if (writing) { event.preventDefault(); pendingWrite(); return; }
    if (link.hasAttribute('data-workspace-full')) {
      event.preventDefault();
      if (await mayLeave(content) && await mayNavigate()) { leaving = true; location.assign(url.href); }
      return;
    }
    if (panel.open && content.contains(link)) {
      event.preventDefault();
      if (busy) { event.preventDefault(); return; }
      if (!link.hasAttribute('data-workspace-preview') && !await mayLeave(content)) { event.preventDefault(); return; }
      if (allowed(url) || (url.pathname === new URL(currentURL || location.href).pathname)) {
        event.preventDefault();
        load(url.href, url.pathname !== new URL(currentURL || location.href).pathname);
      } else {
        event.preventDefault();
        showFeedback('请先关闭面板，再从主导航进入该列表。');
      }
    } else if (allowed(url) && !document.querySelector('main [data-grant-editor]')) {
      event.preventDefault();
      if (!await mayLeave(document.querySelector('main'))) return;
      title.textContent = '正在加载'; back.hidden = true; reload.hidden = true; full.hidden = false;
      openPanel(link, url.pathname === '/console/accounts/new');
      currentURL = url.href;
      full.href = url.href;
      content.replaceChildren();
      load(url.href);
    } else if (!link.getAttribute('href').startsWith('#')) {
      event.preventDefault();
      if (await mayLeave(document.querySelector('main')) && await mayNavigate()) { leaving = true; location.assign(url.href); }
    }
  });

  // 原生表单为无脚本回退；增强时切换每页数量直接查询，沿用未保存保护。
  document.documentElement.classList.add('table-enhanced');
  document.addEventListener('change', event => {
    const select = event.target;
    if (select.matches('[data-page-size-form] select[name="limit"]')) select.form.requestSubmit();
  });
  document.addEventListener('submit', async event => {
    // 已由专属组件处理的表单不再重复导航／提交（如助手异步搜索与发送）。
    if(event.defaultPrevented)return;
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (writing) {
      event.preventDefault();
      const size = form.querySelector('[data-current-size]');
      if (size) size.value = size.dataset.currentSize;
      pendingWrite();
      return;
    }
    if (form.hasAttribute('data-native-submit') && !form.hasAttribute('data-async-dialog')) {
      event.preventDefault();
      const root = panel.open && content.contains(form) ? content : document.querySelector('main');
      // 提交当前表单不是放弃当前草稿；其他表单与上一步草稿仍需保护。
      if (!await mayLeave(root, form)) return;
      if ((event.submitter?.dataset.confirmMessage || form.dataset.confirmMessage) && !await confirmAction(form, event.submitter)) return;
      if (!await mayNavigate()) return;
      const submitter = event.submitter;
      if (submitter?.hasAttribute('formaction')) form.action = submitter.formAction;
      form.dispatchEvent(new CustomEvent('office:submit-approved', {bubbles:true, detail:{submitter}}));
      leaving = true;
      HTMLFormElement.prototype.submit.call(form);
      return;
    }
    const inPanel = panel.open && content.contains(form);
    const inRecord = document.getElementById('record-dialog-content').contains(form);
    if (form.method.toLowerCase() === 'get') {
      event.preventDefault();
      const root = inPanel ? content : document.querySelector('main');
      if (!await mayLeave(root)) {
        event.preventDefault();
        const size = form.querySelector('[data-current-size]');
        if (size) size.value = size.dataset.currentSize;
        return;
      }
      if (inPanel) {
        event.preventDefault();
        const url = new URL(form.getAttribute('action') || currentURL);
        url.search = new URLSearchParams(new FormData(form)).toString();
        load(url.href);
      } else if (await mayNavigate()) { leaving = true; HTMLFormElement.prototype.submit.call(form); }
      else {
        const size = form.querySelector('[data-current-size]');
        if (size) size.value = size.dataset.currentSize;
      }
      return;
    }
    if (!inPanel && !inRecord && !form.dataset.confirmMessage && !form.hasAttribute('data-grant-editor')) {
      event.preventDefault();
      if (await mayLeave(document.querySelector('main'), form) && await mayNavigate()) {
        leaving = true; HTMLFormElement.prototype.submit.call(form);
      }
      return;
    }
    event.preventDefault();
    if (busy) return;
    if (!await mayLeave(document.querySelector('main'), form)) return;
    if (inPanel && !await mayLeave(content, form)) return;
    if (form.dataset.confirmMessage && !await confirmAction(form)) return;
    // 当前授权结束会立即离页；须在发送停用请求之前核对助手草稿。
    if (form.hasAttribute('data-ends-session') && !await mayNavigate()) return;
    if (busy) return;
    const approvedDrafts = captureDirty(document.querySelector('main'), form);
    const submitter = event.submitter;
    const body = new URLSearchParams(new FormData(form));
    if (submitter?.name) body.append(submitter.name, submitter.value);
    busy = true; writing = true;
    const saveController = new AbortController();
    const saveDeadline = setTimeout(() => saveController.abort(), 30000);
    const originalInert = form.inert;
    form.inert = true;
    form.setAttribute('aria-busy', 'true');
    const submitButtons = Array.from(form.querySelectorAll('button[type=submit], button:not([type])')).map(button => [button, button.disabled]);
    submitButtons.forEach(([button]) => button.disabled = true);
    if (inPanel) showFeedback('正在保存，请勿重复提交…', false, true);
    try {
      const response = await fetch(form.getAttribute('action') || location.href, {
        method: 'POST', body, credentials: 'same-origin', signal: saveController.signal, headers: {'Accept': 'text/html'}
      });
      const finalURL = new URL(response.url);
      if (finalURL.origin !== location.origin || !finalURL.pathname.startsWith('/console/')) {
        throw new Error('登录状态已变化，操作结果需核对。请重新登录后检查，勿重复提交。');
      }
      const main = parsePage(await response.text());
      if (!response.ok) {
        const errorText = main.querySelector('.card')?.textContent.trim() || '操作未成功，请核对后重试。';
        throw new Error(errorText);
      }
      if (main.querySelector('h2')?.textContent.trim() === '登录授权已结束') { leaving = true; location.assign('/console/signed-out'); return; }
      if (!main.querySelector('.notice')) throw new Error('未取得保存确认。请重新登录或刷新核对，勿重复提交。');
      // 已取得保存回执的当前表单不再是未保存草稿；发送期间禁止继续修改。
      remember(form);
      savedMessage = main.querySelector('.notice').textContent.trim();
      savedDrafts = approvedDrafts;
      changed = true;
      if (inPanel) {
        if (local) local.holder.querySelector('form')?.reset();
        if (!allowed(finalURL)) {
          remember(form);
          panel.close();
          return;
        }
        const savedScroll = content.scrollTop;
        paint(main, finalURL.href);
        content.scrollTop = savedScroll;
        window.OfficeUI?.notify(savedMessage);
        showFeedback('已保存。关闭面板后列表会更新。');
      } else {
        if (inRecord) { form.reset(); document.getElementById('record-dialog').close(); }
        await refreshList(savedMessage, approvedDrafts);
        savedMessage = null; savedDrafts = undefined;
        changed = false;
      }
    } catch (error) {
      const message = error instanceof TypeError || error.name === 'AbortError'
        ? '网络中断，保存结果暂时无法确认。请刷新核对后再操作，系统不会自动重复提交。'
        : error.message;
      if (inPanel) showFeedback(message, true);
      else {
        let notice = form.parentElement.querySelector('[data-operation-error]');
        if (!notice) {
          notice = document.createElement('p');
          notice.dataset.operationError = '';
          notice.className = 'error-feedback';
          notice.setAttribute('role', 'alert');
          form.parentElement.append(notice);
        }
        notice.textContent = message;
        window.OfficeUI?.notify(message, true);
      }
    } finally {
      clearTimeout(saveDeadline);
      busy = false; writing = false;
      form.removeAttribute('aria-busy');
      form.inert = originalInert;
      submitButtons.forEach(([button, disabled]) => button.disabled = disabled);
    }
  });
  window.addEventListener('beforeunload', event => {
    // close事件异步派发；已关闭且确认放弃的抽屉，不再作为待保存草稿阻止跳转。
    const workspaceDirty = panel.open && (dirty(content) || draftInTrail());
    const recordDialog = document.getElementById('record-dialog');
    const recordDirty = recordDialog?.open && dirty(document.getElementById('record-dialog-content'));
    if (!leaving && (writing || workspaceDirty || dirty(document.querySelector('main')) || recordDirty)) {
      event.preventDefault(); event.returnValue = '';
    }
  });
  window.addEventListener('pageshow', () => { leaving = false; });
})();
