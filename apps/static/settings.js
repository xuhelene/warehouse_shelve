(() => {
  'use strict';
  const $=id=>document.querySelector('#algorithm-settings #'+id), defaults={backend:'copt',time_limit_s:60,threads:8,mip_gap:0.005};
  let current='slotting', drafts={}, saved={}, loaded=false, busy=false, persisted=false, version=0;
  const names={slotting:'入库推荐',picking_path:'拣货路径推荐'};
  function capture(){if(loaded)drafts[current]={backend:$('backend').value,time_limit_s:Number($('time-limit').value),threads:Number($('threads').value),mip_gap:current==='slotting'?Number($('gap').value)/100:drafts[current].mip_gap};}
  function changed(){return JSON.stringify(drafts[current])!==JSON.stringify(saved[current]);}
  function support(){
    const backend=$('backend').value, picking=current==='picking_path';
    $('gap-field').hidden=picking||backend==='heuristic';$('gap').disabled=$('gap-field').hidden;
    $('threads-help').textContent='立体库启发式为单线程，threads及mip_gap不生效。';
    $('time-help').textContent='启发式在规划循环检查截止时间；超时返回失败并保存记录。不含数据准备及结果保存时间。';
    $('backend-help').textContent=backend==='heuristic'?'已接入立体库页面规划，结果不保证全局最优。':'当前立体库规则仅支持启发式；此后端可保存预设，规划时会明确提示尚未支持。';
    $('dirty').textContent=changed()?'有未保存修改':'未修改';
    $('save').disabled=!loaded||busy;
  }
  function render(){
    const value=drafts[current];$('module-title').textContent=names[current];$('save').textContent=current==='slotting'?'保存入库配置':'保存拣货配置';
    $('backend').value=value.backend;$('time-limit').value=value.time_limit_s;$('threads').value=value.threads;$('gap').value=Number((value.mip_gap*100).toFixed(8));
    document.querySelectorAll('[role=tab]').forEach(tab=>{const active=tab.dataset.module===current;tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;if(active)$('panel').setAttribute('aria-labelledby',tab.id);});
    $('algorithm-status').textContent=persisted?'已读取后端配置 · 版本 '+version+'；立体库启发式已接入规划。':'当前显示初始值，尚未保存到后端。';support();
  }
  document.querySelectorAll('[role=tab]').forEach(tab=>{
    tab.onclick=()=>{if(!loaded||busy)return;capture();current=tab.dataset.module;render();};
    tab.onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const tabs=Array.from(document.querySelectorAll('[role=tab]'));const target=event.key==='Home'?tabs[0]:event.key==='End'?tabs[1]:tabs.find(item=>item!==tab);target.click();target.focus();}};
  });
  $('settings-form').oninput=()=>{capture();support();};$('backend').onchange=()=>{capture();support();};
  $('defaults').onclick=()=>{if(!loaded||busy)return;capture();drafts[current]={...defaults};render();$('algorithm-status').textContent='已恢复当前 tab 的初始值，点击保存后写入后端。';};
  $('settings-form').onsubmit=async event=>{
    event.preventDefault();if(!loaded||busy)return;capture();
    if(!$('settings-form').reportValidity())return;
    const module=current, values={...drafts[current]};busy=true;support();$('defaults').disabled=true;$('algorithm-status').textContent='正在保存…';
    try{
      const response=await fetch('/api/settings/'+module,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});const data=await response.json();
      if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'配置无效，请检查数值范围。');
      saved[module]={...data[module]};version=data.version;persisted=true;
      $('algorithm-status').textContent='配置已保存 · 版本 '+version+'；下次规划使用此配置。';
    }catch(error){$('algorithm-status').textContent='保存失败：'+error.message;}
    finally{busy=false;$('defaults').disabled=false;support();}
  };
  async function load(){try{
    const response=await fetch('/api/settings');const data=await response.json();if(!response.ok)throw new Error(data.detail||'请求失败');
    saved={slotting:{...data.slotting},picking_path:{...data.picking_path}};drafts={slotting:{...data.slotting},picking_path:{...data.picking_path}};persisted=data.persisted;version=data.version;loaded=true;render();
  }catch(error){$('algorithm-status').textContent='配置读取失败：'+error.message+' 请刷新重试。';}}
  load();
})();
