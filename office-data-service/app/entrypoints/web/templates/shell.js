/* 桌面收缩偏好与移动菜单独立；存储不可用时仍可在当前页切换。 */
(() => {
  const toggle=document.querySelector('[data-navigation-toggle]');
  const nav=document.querySelector('.shell-nav');
  if(!toggle||!nav)return;
  function setOpen(open,{restore=false}={}) {
    document.documentElement.classList.toggle('navigation-open',open);
    toggle.setAttribute('aria-expanded',String(open));
    toggle.textContent=open?'收起导航':'导航菜单';
    if(restore)toggle.focus();
  }
  toggle.addEventListener('click',()=>setOpen(toggle.getAttribute('aria-expanded')!=='true'));
  document.addEventListener('keydown',event=>{
    // Esc 先交给当前浮层或原生选择器，不能同时关闭其背景导航。
    if(document.querySelector('dialog[open], [data-account-menu][open]')||window.OfficeUI?.pickerOpen())return;
    if(event.key==='Escape'&&!event.defaultPrevented&&toggle.getAttribute('aria-expanded')==='true'){
      setOpen(false,{restore:true});event.preventDefault();
    }
  });
  document.addEventListener('pointerdown',event=>{
    if(document.querySelector('dialog[open]'))return;
    if(!nav.contains(event.target)&&!toggle.contains(event.target))setOpen(false);
  });
  const desktop=matchMedia('(min-width:701px)');
  const collapse=document.querySelector('[data-navigation-collapse]');
  const root=document.documentElement;
  function syncCollapse() {
    const compact=desktop.matches&&root.classList.contains('navigation-collapsed');
    collapse?.setAttribute('aria-expanded',String(!compact));
    const label=compact?'展开导航':'收起导航';
    collapse?.setAttribute('aria-label',label);
    if(collapse)collapse.dataset.tooltip=label;
    nav.querySelectorAll(':scope > a').forEach(link=>{
      if(compact){link.dataset.tooltip=link.textContent.trim();link.dataset.tooltipPlacement='right';}
      else {delete link.dataset.tooltip;delete link.dataset.tooltipPlacement;}
    });
    document.dispatchEvent(new Event('office:navigation-resized'));
  }
  collapse?.addEventListener('click',()=>{
    root.classList.toggle('navigation-collapsed');
    try {localStorage.setItem('office-navigation-collapsed',String(root.classList.contains('navigation-collapsed')));} catch (_) {}
    syncCollapse();
  });
  desktop.addEventListener('change',()=>{setOpen(false);syncCollapse();});
  syncCollapse();
})();
