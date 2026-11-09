(() => {
  'use strict';
  const $ = id => document.getElementById(id), state = {inventory:null, orders:null, specs:null, result:null};
  let busy=false;
  function ready(){ $('plan').disabled=busy||!state.inventory||!state.orders; }
  function invalidate(){state.result=null;const tools=document.querySelector('.route-tools');if(tools)tools.hidden=true;$('result').disabled=true;ready();}
  const apiBase = document.body.dataset.apiBase || '/whshelve/api/slotting';
  const ordersTitle = document.body.dataset.ordersTitle || '入库订单信息';
  const frame = $('layout');
  function syncLayout() {
    const preview = frame.contentWindow?.warehousePreview;
    if (!preview) return;
    const groups = new Map();
    (state.inventory?.rows || []).filter(row => row.slot_id && Number(row.base_quantity)>0).forEach(row=>{
      if(!groups.has(row.slot_id))groups.set(row.slot_id,[]);
      groups.get(row.slot_id).push(row);
    });
    const inventory = Array.from(groups,([slot_id,rows])=>({slot_id, details:rows,
      item_id:rows.map(row=>row.sku_id+' '+row.name).join('；'),
      quantity:rows.map(row=>row.base_quantity+' '+row.base_unit+'（'+row.timestamp.replace('T',' ')+'）').join('；')}));
    preview.setData({inventory,targets:[],paths:[],mode:'inventory'});
    $('layout-status').textContent = '657 个有效货位 · '+new Set(inventory.map(row=>row.slot_id)).size+' 个已占货位 · 备货区 '+(state.inventory?.staging_count || 0)+' 条记录';
  }
  $('warehouse').onchange = () => {frame.src='/whshelve/warehouse-preview?embedded=1&v=20261102-ui8';};
  frame.onload = () => {
    syncLayout();
    if(!frame.contentWindow?.warehousePreview){$('layout-status').textContent=frame.contentDocument?.getElementById('error')?.textContent||'货架布局未能加载，请刷新页面重试。';return;}
    $('top-view').setAttribute('aria-pressed','true');$('perspective-view').setAttribute('aria-pressed','false');
    $('view-level').value='5';
    if($('view-rack'))$('view-rack').value='';
    const summary=frame.contentDocument.getElementById('summary');
    if(summary)new MutationObserver(()=>{if(state.result)$('layout-status').textContent=summary.textContent+(summary.textContent.includes('无目标')?'':' · 绿点可点击查看货位与层号，蓝色短线指向作业货位');}).observe(summary,{childList:true,subtree:true,characterData:true});
  };
  $('perspective-view').onclick=()=>{frame.contentDocument.getElementById('perspective')?.click();$('perspective-view').setAttribute('aria-pressed','true');$('top-view').setAttribute('aria-pressed','false');};
  $('top-view').onclick=()=>{frame.contentDocument.getElementById('top')?.click();$('top-view').setAttribute('aria-pressed','true');$('perspective-view').setAttribute('aria-pressed','false');};
  $('view-level').onchange=()=>{const level=frame.contentDocument.getElementById('level');if(level){level.value=$('view-level').value;level.dispatchEvent(new Event('change'));}};
  if($('view-rack'))$('view-rack').onchange=()=>frame.contentWindow?.warehousePreview?.setRack($('view-rack').value);
  if($('layout-route'))$('layout-route').onchange=()=>{
    frame.contentWindow?.warehousePreview?.setRoute($('layout-route').value);
    const leg=state.result?.legs?.[Number($('layout-route').value)];
    $('route-distance').textContent=leg?'本段 '+Number(leg.distance).toFixed(2)+' 米（含层间距离）':'';
  };
  async function showAlgorithm(settings){
    try {
      if(!settings){const response=await fetch('/whshelve/api/settings');if(!response.ok)throw new Error();settings=await response.json();}
      const picking=apiBase.includes('picking');
      const backend=settings[picking?'picking_path':'slotting'].backend;
      $('algorithm').textContent=({heuristic:'启发式',copt:'COPT（杉数）',ortools:picking?'OR-Tools（当前规则暂不支持）':'OR-Tools'})[backend]||backend;
    }catch{$('algorithm').textContent='配置读取失败，请查看算法设置';}
  }
  showAlgorithm();
  $('warehouse').onchange();
  async function load(kind, file, button) {
    const status = $(kind+'-status');
    status.textContent='正在加载…'; if(button)button.disabled=true;
    try {
      const options = file ? {method:'POST',body:new FormData()} : {};
      if(file)options.body.append('file',file);
      const response=await fetch(apiBase+'/inputs/'+kind+(file?'':'/sample'),options);
      const data=await response.json();
      if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'文件解析失败');
      state[kind]=data;
      invalidate();
      status.textContent=data.filename+' · '+data.count+' 条'+(data.warnings.length?' · '+data.warnings.length+' 项提示':'');
      document.querySelector('[data-details="'+kind+'"]').disabled=false;
      syncLayout();
    } catch(error) {status.textContent='加载失败：'+error.message+(state[kind]?'（保留上次有效数据）':'');}
    finally {if(button)button.disabled=false;}
  }
  document.querySelectorAll('[data-sample]').forEach(button=>button.onclick=()=>load(button.dataset.sample,null,button));
  document.querySelectorAll('input[type=file][data-kind]').forEach(input=>input.onchange=()=>{if(input.files[0])load(input.dataset.kind,input.files[0]);input.value='';});
  $('specs-file').onchange=async event=>{
    const file=event.target.files[0];if(!file)return;
    try{
      const body=new FormData();body.append('file',file);
      const response=await fetch('/whshelve/api/warehouse/specs',{method:'POST',body});const data=await response.json();
      if(!response.ok)throw new Error(data.detail||'规格加载失败');
      state.specs=data;$('specs-status').textContent=data.filename+' · '+data.count+' 条';invalidate();
    }catch(error){$('specs-status').textContent=error.message;}
    event.target.value='';
  };
  $('batch').onchange=invalidate;$('layer-weight').oninput=invalidate;
  $('plan').onclick=async()=>{
    if(busy)return;busy=true;invalidate();
    document.querySelectorAll('input,select,[data-sample]').forEach(el=>el.disabled=true);
    $('message').textContent='正在规划…';
    try{
      const payload={inventory:state.inventory.rows,orders:state.orders.rows,
        storeBatchFlag:$('batch').checked?1:0,layer_weight:Number($('layer-weight').value)};
      if(state.specs)payload.specs=state.specs.rows;
      const response=await fetch(apiBase+'/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      const data=await response.json();if(!response.ok)throw new Error(data.detail||'规划失败');
      state.result=data;$('result').disabled=false;
      $('message').textContent=data.message+(data.total_distance!=null?' · '+Number(data.total_distance).toFixed(2)+' 米':'');
      if(data.status==='failed'||data.shortages?.length||data.unassigned?.length)alert(data.message);
      if(data.status!=='failed'){
        const targets=[...new Set((data.assignments||data.picks||[]).map(row=>row.slot_id))];
        const preview=frame.contentWindow?.warehousePreview;
        // 将米制求解节点映射到示意图；保留前通道、贯通通道与列中心的对应关系。
        const mapPoint=p=>({x:-4.2+(p.x+3.4375)*4.8/5.75,
          y:p.y<=-1.75?(p.y+3.5)/1.75:p.y<.575?1+(p.y+1.75)*1.5/2.325:2+p.y/1.15});
        const legs=data.legs||[];
        const paths=legs.map(leg=>{const points=leg.path.map(mapPoint);return points.length===1?[points[0],points[0]]:points;});
        const pathSlots=legs.map(leg=>[leg.start,leg.end].filter(id=>targets.includes(id)));
        const route=$('layout-route');
        if(route){
          route.replaceChildren(new Option('全部相关路线','-1'));
          legs.forEach((leg,index)=>route.append(new Option((index+1)+'. '+leg.start+' → '+leg.end,String(index))));
          document.querySelector('.route-tools').hidden=!legs.length;$('route-distance').textContent='';
        }
        showAlgorithm(data.settings);
        const grouped=new Map();(data.remaining_inventory||[]).filter(row=>row.slot_id&&Number(row.base_quantity)>0).forEach(row=>{if(!grouped.has(row.slot_id))grouped.set(row.slot_id,[]);grouped.get(row.slot_id).push(row);});
        preview?.setData({inventory:Array.from(grouped,([slot_id,details])=>({slot_id,details,item_id:details.map(r=>r.sku_id+' '+r.name).join('；'),quantity:details.map(r=>r.base_quantity+r.base_unit).join('；')})),targets,paths,pathSlots,targetDetails:data.assignments||data.picks||[],mode:apiBase.includes('picking')?'outbound':'inbound'});
        $('top-view').click();
        $('layout-status').textContent=frame.contentDocument.getElementById('summary').textContent+' · 绿点可点击查看货位与层号，蓝色短线指向作业货位';
      }
    }catch(error){$('message').textContent='规划失败：'+error.message;}
    finally{busy=false;document.querySelectorAll('input,select,[data-sample]').forEach(el=>el.disabled=false);ready();}
  };
  $('result').onclick=()=>{if(state.result)window.open('/whshelve/warehouse-results/'+state.result.run_id,'_blank','noopener');};
  document.querySelectorAll('[data-details]').forEach(button=>button.onclick=()=>{
    const data=state[button.dataset.details];
    const fields=[['sku_id','货品编码'],['name','货品名称'],['location_code','货位编码'],['timestamp','时间'],['quantity','订单数量'],['unit','订单单位'],['conversion_ratio','转换比'],['base_quantity','主单位数量'],['base_unit','主单位']];
    const head=document.querySelector('thead'),body=document.querySelector('tbody');head.replaceChildren();body.replaceChildren();
    const header=document.createElement('tr');fields.forEach(([,label])=>{const cell=document.createElement('th');cell.textContent=label;header.append(cell);});head.append(header);
    data.rows.forEach(row=>{const tr=document.createElement('tr');fields.forEach(([key])=>{const td=document.createElement('td');td.textContent=row[key]??'—';tr.append(td);});body.append(tr);});
    $('warnings').textContent=data.warnings.join('\n');$('details-title').textContent=(button.dataset.details==='inventory'?'当前现存量':ordersTitle)+' · '+data.count+' 条';$('details').showModal();
  });
  $('close-details').onclick=()=>$('details').close();
})();
