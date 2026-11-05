/* 移动导航是可展开区域，不伪装成模态；桌面不改导航状态和键盘顺序。 */
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
  desktop.addEventListener('change',()=>setOpen(false));
})();
