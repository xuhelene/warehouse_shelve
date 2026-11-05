(async()=>{
  'use strict';
  const $=id=>document.getElementById(id),run=location.pathname.split('/').pop();
  const fields=[['order_no','关联单号'],['owner_code','货主'],['sku_id','SKU'],['name','名称'],['slot_id','货位'],['level','层'],['timestamp','入库时间'],['base_quantity','主单位数量'],['base_unit','主单位'],['boxes','箱数'],['tail','尾板'],['mixed','混托'],['appended','追加'],['grade','周转等级'],['trip','送出口趟次']];
  function table(id,rows,columns){
    const target=$(id),head=target.createTHead().insertRow();
    columns.forEach(([,label])=>{const cell=document.createElement('th');cell.textContent=label;head.append(cell);});
    const body=target.createTBody();rows.forEach(row=>{const line=body.insertRow();columns.forEach(([key])=>{line.insertCell().textContent=typeof row[key]==='boolean'?(row[key]?'是':'否'):row[key]??'—';});});
  }
  try{
    const response=await fetch('/whshelve/api/warehouse/runs/'+run),data=await response.json();if(!response.ok)throw new Error(data.detail||'读取失败');
    $('title').textContent=data.operation==='slotting'?'入库规划结果':'出库规划结果';
    $('summary').textContent=data.message+(data.total_distance!=null?' · '+Number(data.total_distance).toFixed(2)+' 米':'');
    $('meta').textContent='状态：'+({feasible:'可行解（未证明最优）',partial:'部分满足',failed:'失败'}[data.status]||data.status)+' · 任务 '+run+' · 文件快照 · 权重 '+data.layer_weight;
    $('warnings').textContent=(data.warnings||[]).filter(warning=>warning!=='启发式为单线程；threads及mip_gap不生效，结果不保证全局最优。').join('\n');
    const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});$('download').href=URL.createObjectURL(blob);$('download').download=run+'.json';
    const details=[...(data.assignments||data.picks||[])];
    details.sort((a,b)=>{
      for(const key of ['order_no','sku_id','slot_id']){
        const comparison=String(a[key]??'').localeCompare(String(b[key]??''),'zh-CN',{numeric:true});
        if(comparison)return comparison;
      }
      return 0;
    });
    table('details-table',details,fields);
    table('inventory-table',data.remaining_inventory||[],fields.slice(0,10));
    table('shortages-table',[...(data.shortages||[]),...(data.unassigned||[])],[['order_no','关联单号'],['owner_code','货主'],['sku_code','缺货SKU'],['sku_id','未分配SKU'],['sku_name','名称'],['requested_quantity','需求'],['fulfilled_quantity','满足'],['shortage_quantity','缺货'],['unit','单位'],['base_quantity','未分配主单位数量'],['base_unit','主单位'],['reason','原因']]);
  }catch(error){$('summary').textContent=error.message;}
})();
