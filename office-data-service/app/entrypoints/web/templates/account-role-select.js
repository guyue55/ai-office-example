/* 开户与关联共用角色选择；更新选项不提交表单，不覆盖当前选择。 */
(() => {
  const initialized = new WeakSet();
  function setup() { document.querySelectorAll('[data-account-role-picker]').forEach(root => {
    if (initialized.has(root)) return;
    initialized.add(root);
    const select = root.querySelector('[data-role-select]');
    const refresh = root.querySelector('[data-role-refresh]');
    const search = root.querySelector('[data-role-search-button]');
    const query = root.querySelector('[data-role-query]');
    const status = root.querySelector('[data-role-status]');
    refresh.hidden = false;
    root.querySelector('[data-role-search]').hidden = false;
    let pending = false;
    async function load() {
      if (pending) return;
      pending = true;
      refresh.disabled = search.disabled = true;
      status.textContent = '正在读取角色…';
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch('/console/accounts/role-options?q=' + encodeURIComponent(query.value), {signal: controller.signal, headers: {'Accept': 'application/json'}});
        if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('unavailable');
        const result = await response.json();
        if (!Array.isArray(result.items)) throw new Error('invalid');
        const selected = select.selectedOptions[0]?.cloneNode(true);
        const options = [new Option('普通员工（默认）', '')];
        result.items.forEach(role => {
          if (role.builtin_key !== 'employee') options.push(new Option(role.name + ' · ' + (role.permission_count || 0) + ' 项权限', role.id));
        });
        if (selected?.value && !options.some(option => option.value === selected.value)) options.push(selected);
        const value = select.value;
        select.replaceChildren(...options);
        select.value = value;
        status.textContent = result.has_more ? '当前仅显示前100个匹配角色，请缩小搜索范围；当前选择已保留。' : '角色选项已更新，当前选择已保留。';
      } catch (_) {
        status.textContent = '角色读取失败，原选择已保留。请重试；如已退出登录，请重新登录。';
      } finally {
        clearTimeout(timeout);
        pending = false;
        refresh.disabled = search.disabled = false;
      }
    }
    refresh.addEventListener('click', load);
    search.addEventListener('click', load);
    query.addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); load(); }
    });
  }); }
  setup();
  new MutationObserver(setup).observe(document.body, {childList: true, subtree: true});
})();
