/* 固定解析器仅负责Markdown分词；只用允许的DOM节点，不把模型字符串作为HTML。 */
(() => {
  const api=window.OfficeAssistant;
  if(!api)return;
  const parser=window.markdownit?.({html:false,linkify:false,typographer:false,maxNesting:16}).disable('image');
  const tags=new Set(['p','strong','em','s','blockquote','ul','ol','li','h1','h2','h3','h4','h5','h6','table','thead','tbody','tr','th','td','a']);
  function markdown(text){
    const root=document.createElement('div');root.className='assistant-answer assistant-markdown';
    if(!parser||text.length>32000){root.textContent=text;return root;}
    let count=0;
    function append(tokens,parent){
      const stack=[parent];
      for(const token of tokens){
        if(++count>6000)throw new Error('展示节点超出预算');
        const current=()=>stack[stack.length-1];
        if(token.type==='inline'){append(token.children||[],current());continue;}
        if(token.nesting===-1){if(stack.length>1)stack.pop();continue;}
        if(token.nesting===1){
          const node=document.createElement(tags.has(token.tag)?token.tag:'span');
          if(token.tag==='a'){
            // 再次限制协议；不信任解析器／模型给出的属性、事件和样式。
            const href=token.attrGet('href')||'';
            try{const url=new URL(href,location.origin);if(['http:','https:'].includes(url.protocol)&&!url.username&&!url.password){node.href=url.href;node.target='_blank';node.rel='noopener noreferrer';}}catch(_){}
          }
          if(token.tag==='ol'){const start=token.attrGet('start');if(start&&/^\d{1,6}$/.test(start))node.start=Number(start);}
          current().append(node);stack.push(node);continue;
        }
        if(token.type==='fence'||token.type==='code_block'){
          const pre=document.createElement('pre'),code=document.createElement('code');code.textContent=token.content;pre.append(code);current().append(pre);
        }else if(token.type==='code_inline'){const node=document.createElement('code');node.textContent=token.content;current().append(node);}
        else if(token.type==='softbreak'||token.type==='hardbreak')current().append(document.createElement('br'));
        else if(token.type==='hr')current().append(document.createElement('hr'));
        else current().append(document.createTextNode(token.content||''));
      }
    }
    try{append(parser.parse(text,{}),root);}catch(_){root.replaceChildren(document.createTextNode(text));}
    root.querySelectorAll('table').forEach(table=>{const scroll=document.createElement('div');scroll.className='assistant-table-scroll';scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label','回复中的表格，可横向滚动');table.replaceWith(scroll);scroll.append(table);});
    return root;
  }
  async function copy(text){
    try{if(!navigator.clipboard?.writeText)throw new Error('剪贴板不可用');await navigator.clipboard.writeText(text);window.OfficeUI.notify('已复制原文');}
    catch(_){
      // 局域网HTTP或浏览器拒绝剪贴板时，由本人选择复制；不谎报成功。
      const box=document.createElement('div'),note=document.createElement('p'),area=document.createElement('textarea');
      note.textContent='浏览器未允许自动复制。原文已选中，请按 Ctrl / ⌘ + C 复制。';area.value=text;area.readOnly=true;area.rows=10;area.setAttribute('aria-label','待复制原文');box.append(note,area);
      if(window.OfficeUI.present('复制原文',box)){area.focus();area.select();}else window.OfficeUI.notify('请先关闭当前弹窗，再复制。',true);
    }
  }
  api.content={markdown,copy};
})();
