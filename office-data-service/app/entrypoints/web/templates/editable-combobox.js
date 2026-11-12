/* 可编辑单选组件：仅安全文本，支持自由输入、键盘与有界候选呈现。 */
(() => {
  'use strict';
  window.OfficeCombobox = {
    create(input, list) {
      let items = [], visible = [], active = -1;
      input.setAttribute('role', 'combobox');
      input.setAttribute('aria-controls', list.id);
      input.setAttribute('aria-autocomplete', 'list');
      input.setAttribute('aria-expanded', 'false');
      function close() {
        list.hidden = true; active = -1;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
      }
      /* 打开候选与过滤候选是两件事：展开（点按钮、按方向键、重新聚焦）
         应当列出全部候选，否则选中过一项后再也看不到别的模型；
         只有真正在输入时，才按当前文本过滤。 */
      function draw(query) {
        const needle = (query === undefined ? '' : query).trim().toLowerCase();
        visible = (needle
          ? items.filter(item => item.id.toLowerCase().includes(needle) || item.name.toLowerCase().includes(needle))
          : items.slice()).slice(0, 50);
        if (input.value && !items.some(item => item.id === input.value)) visible.push({id:input.value, name:'使用此自定义模型ID'});
        list.replaceChildren(); active = -1;
        visible.forEach((item, index) => {
          const row = document.createElement('div');
          row.setAttribute('role', 'option'); row.setAttribute('aria-selected', 'false');
          row.id = list.id + '-' + index; row.dataset.index = String(index);
          row.textContent = item.id + (item.name !== item.id ? ' · ' + item.name : '');
          list.append(row);
        });
        list.hidden = !visible.length;
        input.setAttribute('aria-expanded', String(!!visible.length));
      }
      function choose(index) {
        if (!visible[index]) return;
        input.value = visible[index].id; close();
        input.dispatchEvent(new Event('input', {bubbles:true}));
        input.dispatchEvent(new Event('change', {bubbles:true})); close(); input.focus();
      }
      input.addEventListener('input', () => draw(input.value));
      input.addEventListener('focus', () => draw());
      input.addEventListener('blur', close);
      const toggle = input.parentElement.querySelector('[data-combobox-toggle]');
      toggle?.addEventListener('mousedown', event => event.preventDefault());
      toggle?.addEventListener('click', () => {
        // 展开按钮的语义是「看全部候选」：先判打开态再决定，不要用 focus 触发的
        // 输入态重画结果反过来影响判断，否则输入过文本后点按钮会变成关闭。
        if (!list.hidden) { close(); return; }
        input.focus();
        draw();
      });
      input.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !list.hidden) {event.preventDefault(); event.stopPropagation(); close();}
        else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault(); if (list.hidden) draw(); if (!visible.length) return;
          active = (active + (event.key === 'ArrowDown' ? 1 : -1) + visible.length) % visible.length;
          Array.from(list.children).forEach((row,index) => row.setAttribute('aria-selected', String(index === active)));
          const row = list.children[active]; input.setAttribute('aria-activedescendant', row.id); row.scrollIntoView({block:'nearest'});
        } else if (event.key === 'Enter' && !list.hidden && active >= 0) {event.preventDefault(); choose(active);}
        else if (event.key === 'Tab') close();
      });
      list.addEventListener('mousedown', event => event.preventDefault());
      list.addEventListener('click', event => {
        const row = event.target.closest('[data-index]'); if (row) choose(Number(row.dataset.index));
      });
      return {setItems(next) {items = next; if (document.activeElement === input) draw();}, close};
    }
  };
})();
