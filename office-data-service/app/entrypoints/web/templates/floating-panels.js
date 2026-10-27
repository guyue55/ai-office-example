/* 共享非模态浮层：显式语义、顶层定位与关闭／焦点，不参与业务执行。 */
(() => {
  const ui=window.OfficeUI;
  if(!ui)return;
  let opened=null;
  const panels=new Set();
  const visible=node=>!!node.getClientRects().length;
  function position(panel,trigger) {
    const rect=trigger.getBoundingClientRect();
    const height=panel.offsetHeight,width=panel.offsetWidth;
    panel.style.left=Math.max(12,Math.min(innerWidth-width-12,rect.right-width))+'px';
    panel.style.top=Math.max(12,rect.bottom+height+8<innerHeight-12?rect.bottom+8:rect.top-height-8)+'px';
  }
  function closePopovers(){opened?.close();}
  function floating(trigger,panel,{initialFocus,onOpen}={}) {
    const focusable=node=>node instanceof HTMLElement&&panel.contains(node)&&!node.matches(':disabled')&&!node.closest('[hidden],[inert]')&&visible(node)&&getComputedStyle(node).visibility!=='hidden';
    function focusPanel(){
      const requested=initialFocus?.();
      const candidates=Array.from(panel.querySelectorAll('input:not([type="hidden"]),button,select,textarea,a[href],[tabindex]')).filter(node=>focusable(node)&&node.tabIndex>=0);
      // 显式初始目标也需验证；跳过禁用／隐藏控件，不能把焦点留在浮层外。
      const target=focusable(requested)&&(requested.tabIndex>=0||requested.hasAttribute('tabindex'))?requested:candidates[0]||panel;
      if(target===panel&&!panel.hasAttribute('tabindex'))panel.tabIndex=-1;
      target.focus({preventScroll:true});
    }
    trigger.setAttribute('aria-haspopup','dialog');trigger.setAttribute('aria-controls',panel.id);trigger.setAttribute('aria-expanded','false');
    const native=typeof panel.showPopover==='function';
    const control={
      get open(){return opened===control;},
      close(restore=true){
        if(!control.open)return;
        if(native&&panel.matches(':popover-open'))panel.hidePopover();
        delete panel.dataset.floatingOpen;trigger.setAttribute('aria-expanded','false');opened=null;
        if(restore&&trigger.isConnected&&visible(trigger))trigger.focus({preventScroll:true});
      },
      show(){
        if(document.querySelector('dialog[open]')||trigger.disabled)return;
        opened?.close(false);opened=control;panel.dataset.floatingOpen='';
        if(native)panel.showPopover();position(panel,trigger);trigger.setAttribute('aria-expanded','true');
        focusPanel();onOpen?.();
      },
      position(){if(control.open)position(panel,trigger);}
    };
    panels.add(control);
    trigger.addEventListener('click',()=>control.open?control.close():control.show());
    panel.querySelectorAll('[data-floating-close]').forEach(button=>button.addEventListener('click',()=>control.close()));
    panel.addEventListener('click',event=>{if(event.target.closest('[data-floating-dismiss]'))control.close();},true);
    document.addEventListener('pointerdown',event=>{
      if(control.open&&!panel.contains(event.target)&&!trigger.contains(event.target))control.close(false);
    },true);
    document.addEventListener('keydown',event=>{
      if(!control.open||event.defaultPrevented||document.querySelector('dialog[open]')||ui.pickerOpen())return;
      if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();control.close();}
    },true);
    document.addEventListener('focusin',event=>{
      if(control.open&&!panel.contains(event.target)&&!trigger.contains(event.target))control.close(false);
    });
    return control;
  }
  window.addEventListener('resize',()=>panels.forEach(panel=>panel.position()));
  window.addEventListener('scroll',()=>panels.forEach(panel=>panel.position()),true);
  ui.floating=floating;ui.closePopovers=closePopovers;
})();
