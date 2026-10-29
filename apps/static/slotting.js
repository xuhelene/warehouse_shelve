(() => {
  'use strict';
  const $ = id => document.getElementById(id), state = {inventory:null, orders:null, specs:null, result:null};
  let busy=false;
  function ready(){ $('plan').disabled=busy||!state.inventory||!state.orders; }
  function invalidate(){state.result=null;$('result').disabled=true;ready();}
  const apiBase = document.body.dataset.apiBase || '/api/slotting';
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
  $('warehouse').onchange = () => {frame.src='/warehouse-preview?embedded=1';};
  frame.onload = () => {
    syncLayout();
    $('view-level').value='5';
    if($('view-rack'))$('view-rack').value='';
    const detail=frame.contentDocument.getElementById('detail');
    if(detail)new MutationObserver(()=>{$('slot-detail').textContent=detail.textContent;}).observe(detail,{childList:true,subtree:true,characterData:true});
  };
  $('perspective-view').onclick=()=>frame.contentDocument.getElementById('perspective')?.click();
  $('top-view').onclick=()=>frame.contentDocument.getElementById('top')?.click();
  $('view-level').onchange=()=>{const level=frame.contentDocument.getElementById('level');if(level){level.value=$('view-level').value;level.dispatchEvent(new Event('change'));}};
  if($('view-rack'))$('view-rack').onchange=()=>frame.contentWindow?.warehousePreview?.setRack($('view-rack').value);
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
      const response=await fetch('/api/warehouse/specs',{method:'POST',body});const data=await response.json();
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
        const grouped=new Map();(data.remaining_inventory||[]).filter(row=>row.slot_id&&Number(row.base_quantity)>0).forEach(row=>{if(!grouped.has(row.slot_id))grouped.set(row.slot_id,[]);grouped.get(row.slot_id).push(row);});
        preview?.setData({inventory:Array.from(grouped,([slot_id,details])=>({slot_id,details,item_id:details.map(r=>r.sku_id+' '+r.name).join('；'),quantity:details.map(r=>r.base_quantity+r.base_unit).join('；')})),targets,paths:[],mode:apiBase.includes('picking')?'outbound':'inbound'});
        $('layout-status').textContent='推荐后预计库存 · 实际通道路径及取货明细请查看结果';
      }
    }catch(error){$('message').textContent='规划失败：'+error.message;}
    finally{busy=false;document.querySelectorAll('input,select,[data-sample]').forEach(el=>el.disabled=false);ready();}
  };
  $('result').onclick=()=>{if(state.result)window.open('/warehouse-results/'+state.result.run_id,'_blank','noopener');};
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
