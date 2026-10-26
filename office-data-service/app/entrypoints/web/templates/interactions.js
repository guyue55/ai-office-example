(() => {
  'use strict';
  const dialog = document.getElementById('record-dialog');
  const holder = document.getElementById('record-dialog-content');
  const heading = document.getElementById('record-dialog-title');
  const region = document.getElementById('ui-notifications');
  const tip = document.getElementById('ui-tooltip');
  if (!dialog?.showModal) return;
  document.documentElement.classList.add('ui-enhanced');
  let local, tipTarget, tipTimer, tipHideTimer, lastFocusKey, confirmationPending = false;
  const icons = {
    account: ['M3 5h18v14H3Z','M7 9h3v3H7Z','M14 9h4M14 13h4M7 16h11'],
    eye: ['M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12','M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6'],
    person: ['M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2','M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8','M17 8h5m-2.5-2.5v5'],
    settings: ['M4 7h16M4 17h16','M8 4v6m8 4v6'],
    edit: ['m16 3 5 5-12 12-6 1 1-6Z','m14 5 5 5'],
    stop: ['M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20','m5 5 14 14'],
    archive: ['M3 3h18v5H3Z','M5 8v13h14V8','M9 12h6'],
    retry: ['M20 7a9 9 0 1 0 1 9','M20 2v5h-5'],
    play: ['m8 4 12 8-12 8Z'],
    clock: ['M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20','M12 6v6l4 2'],
    more: ['M4 12h2m5 0h2m5 0h2'],
    copy: ['M9 9h11v12H9Z','M5 16H3V3h12v2'],
    close: ['m6 6 12 12M6 18 18 6'],
    collapse: ['M3 4h18v16H3Z','M15 4v16','m7 8 4 4-4 4'],
    chevron: ['m7 10 5 5 5-5'],
    info: ['M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20','M12 11v6M12 7v1'],
    down: ['M12 4v16m-6-6 6 6 6-6']
  };
  function iconFor(label) {
    if (/归档/.test(label)) return 'archive';
    if (/重试/.test(label)) return 'retry';
    if (/账号详情/.test(label)) return 'account';
    if (/停用|撤销|删除/.test(label)) return 'stop';
    if (/启用/.test(label)) return 'play';
    if (/修改|编辑/.test(label)) return 'edit';
    if (/详情|内容|查看|原始数据/.test(label)) return 'eye';
    if (/角色|授权/.test(label)) return 'person';
    if (/配置|权限集|权限/.test(label)) return 'settings';
    if (/进度/.test(label)) return 'clock';
    return 'more';
  }
  // 保留原生文本作为无脚本回退；增强时同时保留读屏名称。
  function iconAction(control) {
    if (control.hasAttribute('data-icon-action')) return;
    if (control.tagName !== 'SUMMARY' && control.closest('details')) return;
    const label = control.textContent.trim();
    if (!label) return;
    control.dataset.iconAction = '';
    control.dataset.tooltip = label;
    control.setAttribute('aria-label', label);
    control.classList.add('icon-action');
    const focusKey = control.getAttribute('href') || control.closest('form')?.getAttribute('action') || control.parentElement.querySelector('form')?.getAttribute('action');
    // 同一员工的姓名编辑与启停可能共用地址，按控件类型区分恢复目标。
    const actionKind = control.parentElement.querySelector('input[name=action]')?.value || '';
    if (focusKey) control.dataset.focusKey = focusKey + '|' + control.tagName + '|' + actionKind;
    const svg = icon(iconFor(label));
    const text = document.createElement('span'); text.className = 'sr-only'; text.textContent = label;
    control.replaceChildren(svg, text);
  }
  // 图标显式命名；原表格增强器与新组件共用SVG，不靠新文案猜操作。
  function icon(name) {
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.setAttribute('focusable','false');
    for(const d of icons[name]||icons.more){const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',d);svg.append(path);}
    return svg;
  }
  function decorateIcon(control,name,label,{text=false}={}) {
    control.dataset.iconAction='';control.setAttribute('aria-label',label);
    control.classList.add(text?'ui-text-action':'ui-icon-button');
    if(!text)control.dataset.tooltip=label;
    control.removeAttribute('title');
    const span=document.createElement('span');span.textContent=label;if(!text)span.className='sr-only';
    control.replaceChildren(icon(name),span);return control;
  }
  function iconButton(name,label,action) {
    const control=document.createElement('button');control.type='button';decorateIcon(control,name,label);
    control.addEventListener('click',action);return control;
  }
  function notify(message, error = false) {
    const notice = document.createElement('div');
    notice.className = 'notice ui-notice' + (error ? ' error-feedback' : '');
    notice.setAttribute('role', error ? 'alert' : 'status');
    const text = document.createElement('span'); text.textContent = message;
    const close = document.createElement('button'); close.type = 'button'; close.className = 'ui-notice-close'; close.setAttribute('aria-label', '关闭通知'); close.textContent = '×';
    notice.append(text, close); region.append(notice);
    if (region.showPopover && !region.matches(':popover-open')) region.showPopover();
    close.onclick = () => notice.remove();
    while (region.children.length > 3) region.firstElementChild.remove();
    let timer;
    const pause = () => clearTimeout(timer);
    const resume = () => { pause(); if (!error) timer = setTimeout(() => notice.remove(), 8000); };
    notice.addEventListener('mouseenter', pause); notice.addEventListener('mouseleave', resume);
    notice.addEventListener('focusin', pause); notice.addEventListener('focusout', resume); resume();
    return notice;
  }
  function adoptNotices(root) {
    root.querySelectorAll('main > .notice:not([data-notice-adopted])').forEach(notice => {
      notice.dataset.noticeAdopted = '';
      notify(notice.textContent.trim(), notice.classList.contains('error-feedback')); notice.remove();
    });
  }
  function enhance(root) {
    root.querySelectorAll('table').forEach(table => {
      const headers = table.querySelectorAll('thead tr:first-child th');
      const indexes = Array.from(headers).map((th, index) => /^(操作|详情)$/.test(th.textContent.trim()) ? index : -1).filter(index => index >= 0);
      table.querySelectorAll('tbody tr').forEach(row => indexes.forEach(index => {
        const cell = row.cells[index];
        if (!cell || cell.colSpan > 1) return;
        cell.classList.add('action-cell');
        cell.querySelectorAll('a,button[type=submit],summary').forEach(iconAction);
      }));
      table.querySelectorAll('tbody details > summary').forEach(summary => {
        summary.dataset.tooltip ||= '点击查看' + (summary.getAttribute('aria-label') || summary.textContent.trim());
        summary.setAttribute('aria-haspopup', 'dialog');
      });
    });
    adoptNotices(root);
  }
  function hideTip() {
    clearTimeout(tipTimer); clearTimeout(tipHideTimer);
    if (tipTarget) {
      const ids = (tipTarget.getAttribute('aria-describedby') || '').split(' ').filter(id => id && id !== tip.id);
      if (ids.length) tipTarget.setAttribute('aria-describedby', ids.join(' ')); else tipTarget.removeAttribute('aria-describedby');
    }
    tipTarget = null;
    if (tip.matches(':popover-open')) tip.hidePopover();
  }
  function showTip(target, keyboard = false) {
    // 触屏不依赖悬停；接入键盘时仍须展示聚焦提示。
    if (!target?.dataset.tooltip || target.disabled || (!keyboard && matchMedia('(hover: none)').matches)) return;
    hideTip();
    tipTimer = setTimeout(() => {
      if (!target.isConnected || target.disabled) return;
      tipTarget = target; tip.textContent = target.dataset.tooltip;
      if(target.getAttribute('aria-label')!==target.dataset.tooltip)target.setAttribute('aria-describedby', [target.getAttribute('aria-describedby'), tip.id].filter(Boolean).join(' '));
      if (tip.showPopover) tip.showPopover();
      const rect = target.getBoundingClientRect();
      const width = tip.offsetWidth, height = tip.offsetHeight;
      tip.style.left = Math.max(8, Math.min(innerWidth-width-8, rect.x+rect.width/2-width/2)) + 'px';
      tip.style.top = (rect.top > height+12 ? rect.top-height-8 : rect.bottom+8) + 'px';
    }, 150);
  }
  document.addEventListener('pointerover', event => {
    const target = event.target.closest('[data-tooltip]');
    if (target && !target.contains(event.relatedTarget)) showTip(target);
  });
  document.addEventListener('pointerout', event => {
    const target = event.target.closest('[data-tooltip]');
    if (target && !target.contains(event.relatedTarget)) tipHideTimer = setTimeout(() => {
      if (!document.activeElement?.matches('[data-tooltip]')) hideTip();
    }, 150);
  });
  tip.addEventListener('pointerenter', () => clearTimeout(tipHideTimer));
  tip.addEventListener('pointerleave', () => {if (!document.activeElement?.matches('[data-tooltip]')) hideTip();});
  document.addEventListener('focusin', event => showTip(event.target.closest('[data-tooltip]'), true));
  document.addEventListener('focusout', hideTip);
  function repositionTip() {
    const focused = document.activeElement?.closest('[data-tooltip]');
    hideTip();
    // 键盘聚焦可能引发浏览器自动滚动；不能因此取消尚未显示的提示。
    if (focused) {
      const rect = focused.getBoundingClientRect();
      if (rect.top >= 0 && rect.bottom <= innerHeight) showTip(focused, true);
    }
  }
  window.addEventListener('scroll', repositionTip, true); window.addEventListener('resize', repositionTip);
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && tipTarget) { hideTip(); event.preventDefault(); event.stopPropagation(); } }, true);
  // 所有站内确认复用同一组件；重复点击不替换仍未完成的确认。
  function confirm(message, options = {}) {
    const box = document.getElementById('action-confirm');
    if (!box || box.open || confirmationPending) return Promise.resolve(false);
    confirmationPending = true;
    window.OfficeUI.closePopovers?.();
    const previous = document.activeElement;
    const cancel = box.querySelector('[data-confirm-cancel]');
    const accept = box.querySelector('[data-confirm-accept]');
    box.querySelector('#confirm-title').textContent = options.title || '确认操作';
    box.querySelector('#confirm-description').textContent = message;
    accept.textContent = options.accept || (message.startsWith('确认停用') ? '确认停用' : message.startsWith('确认重新启用') ? '确认启用' : message.startsWith('确认撤销') ? '确认撤销' : message.startsWith('确认归档') ? '确认归档' : message.startsWith('确认重试') ? '确认重试' : '确认操作');
    accept.classList.toggle('danger', options.danger ?? /停用|撤销|删除|归档/.test(message));
    box.showModal(); cancel.focus();
    return new Promise(resolve => {
      let accepted = false;
      const close = () => {
        confirmationPending = false;
        cancel.onclick = null; accept.onclick = null; box.oncancel = null;
        if (previous?.isConnected) previous.focus({preventScroll:true});
        resolve(accepted);
      };
      box.addEventListener('close', close, {once:true});
      cancel.onclick = () => box.close();
      accept.onclick = () => { accepted = true; box.close(); };
      box.oncancel = event => { event.preventDefault(); box.close(); };
    });
  }
  async function closeRecord() {
    if (holder.querySelector('[aria-busy=true]')) return;
    if (window.OfficeForms?.dirty(holder)) {
      if (!await confirm('修改尚未保存。确定关闭并放弃修改吗？', {title:'放弃未保存的修改？', accept:'放弃修改并关闭', danger:true})) return;
      holder.querySelectorAll('form').forEach(form => form.reset());
    }
    dialog.close();
  }
  dialog.querySelector('[data-record-close]').addEventListener('click', closeRecord);
  dialog.addEventListener('cancel', event => { event.preventDefault(); closeRecord(); });
  dialog.addEventListener('close', () => {
    if (local) {
      local.source.append(...Array.from(holder.childNodes)); local.source.open = false;
      const trigger = local.trigger; local = null;
      document.body.classList.remove('record-open'); trigger.focus({preventScroll:true});
    }
  });
  // 移动现有节点，保留表单及版本字段；不克隆、不扩大服务端权限。
  document.addEventListener('click', event => {
    hideTip();
    const action = event.target.closest('[data-focus-key]');
    if (action) lastFocusKey = action.dataset.focusKey;
    const summary = event.target.closest('details > summary');
    if (!summary || !(summary.closest('tbody') || (summary.closest('#workspace') && summary.parentElement.hasAttribute('data-form-dialog')))) return;
    if (dialog.open) return;
    event.preventDefault(); event.stopImmediatePropagation();
    const source = summary.parentElement;
    local = {source, trigger:summary};
    lastFocusKey = summary.dataset.focusKey;
    heading.textContent = summary.getAttribute('aria-label') || summary.textContent.trim();
    holder.replaceChildren(...Array.from(source.childNodes).filter(node => node !== summary));
    holder.querySelectorAll('form').forEach(form => {
      window.OfficeForms.remember(form);
      if ((form.getAttribute('action') || '').includes('/client-authorizations/')) form.dataset.asyncDialog = '';
    });
    dialog.showModal(); document.body.classList.add('record-open'); heading.focus();
  }, true);
  const restoreFocus = () => {
    const target = lastFocusKey && Array.from(document.querySelectorAll('main [data-focus-key]')).find(item => item.dataset.focusKey === lastFocusKey);
    if (target) target.focus({preventScroll:true});
    lastFocusKey = null; return !!target;
  };
  function present(title, content) {
    if(dialog.open)return false;
    window.OfficeUI.closePopovers?.();
    const previous=document.activeElement;
    heading.textContent=title;holder.replaceChildren(content);
    dialog.addEventListener('close',()=>{holder.replaceChildren();previous?.isConnected&&previous.focus({preventScroll:true});},{once:true});
    dialog.showModal();heading.focus();return true;
  }
  window.OfficeUI = {enhance, notify, confirm, closeRecord, restoreFocus, present, icon, decorateIcon, iconButton};
  enhance(document);
  let pending = false;
  new MutationObserver(records => {
    if (pending || records.every(record => region.contains(record.target) || tip.contains(record.target))) return;
    pending = true;
    queueMicrotask(() => { pending = false; enhance(document); });
  }).observe(document.body, {childList:true, subtree:true});
})();
