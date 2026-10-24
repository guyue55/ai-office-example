(() => {
  'use strict';
  // 原生展开负责基本操作；仅增强外部关闭和键盘焦点，不接管退出请求。
  document.querySelectorAll('[data-account-menu]').forEach(menu => {
    const trigger = menu.querySelector('summary');
    document.addEventListener('pointerdown', event => {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !menu.open) return;
      menu.open = false;
      trigger.focus();
      event.preventDefault();
    });
    menu.addEventListener('focusout', () => {
      window.setTimeout(() => {
        if (!menu.contains(document.activeElement)) menu.open = false;
      }, 0);
    });
  });
})();
