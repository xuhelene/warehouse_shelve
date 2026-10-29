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
    const response=await fetch('/api/warehouse/runs/'+run),data=await response.json();if(!response.ok)throw new Error(data.detail||'读取失败');
    $('title').textContent=data.operation==='slotting'?'入库规划结果':'出库规划结果';
    $('summary').textContent=data.message+(data.total_distance!=null?' · '+Number(data.total_distance).toFixed(2)+' 米':'');
    $('meta').textContent='状态：'+({feasible:'可行解（未证明最优）',partial:'部分满足',failed:'失败'}[data.status]||data.status)+' · 任务 '+run+' · 文件快照 · 权重 '+data.layer_weight;
    $('warnings').textContent=(data.warnings||[]).join('\n');
    const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});$('download').href=URL.createObjectURL(blob);$('download').download=run+'.json';
    table('details-table',data.assignments||data.picks||[],fields);
    table('inventory-table',data.remaining_inventory||[],fields.slice(0,10));
    table('shortages-table',[...(data.shortages||[]),...(data.unassigned||[])],[['order_no','关联单号'],['owner_code','货主'],['sku_code','缺货SKU'],['sku_id','未分配SKU'],['sku_name','名称'],['requested_quantity','需求'],['fulfilled_quantity','满足'],['shortage_quantity','缺货'],['unit','单位'],['base_quantity','未分配主单位数量'],['base_unit','主单位'],['reason','原因']]);
    const legs=data.legs||[],svg=$('map'),ns='http://www.w3.org/2000/svg';
    function node(tag,attrs){const el=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,v));svg.append(el);return el;}
    const point=p=>[p.x*6,-p.y];
    function draw(){
      svg.replaceChildren();svg.setAttribute('viewBox','-26 -55 47 65');
      // 通道图与算法使用同一份米制节点、边及货位映射。
      for(const edge of data.layout?.edges||[]){const a=point(data.layout.nodes[edge.start]),b=point(data.layout.nodes[edge.end]);node('line',{x1:a[0],y1:a[1],x2:b[0],y2:b[1],stroke:'#bccad6','stroke-width':.7});}
      for(const [rack,x] of [['C14',-1.125],['C15',0],['C16',4.625]]){
        const center=x*6;for(const [y,h] of [[0,21.85],[25.3,26.45]])node('rect',{x:center-3,y:-y-h,width:6,height:h,fill:'#ddb98b',opacity:.7});
        node('text',{x:center,y:2,'font-size':2,'text-anchor':'middle'}).textContent=rack;
      }
      const index=Number($('route-select').value),shown=index<0?legs:[legs[index]].filter(Boolean);
      shown.forEach((leg,i)=>{
        node('polyline',{points:leg.path.map(p=>point(p).join(',')).join(' '),fill:'none',stroke:`hsl(${i*67%360} 70% 40%)`,'stroke-width':.35});
        const end=point(leg.path[leg.path.length-1]);node('circle',{cx:end[0],cy:end[1],r:.65,fill:'#ea7b32'});
      });
      node('text',{x:-3.375,y:6,'font-size':1.8,'text-anchor':'middle'}).textContent='备货区 / 出口';
      $('route-info').textContent=shown.map(leg=>leg.start+' → '+leg.end+'\n'+Number(leg.distance).toFixed(2)+' 米（通道 '+Number(leg.horizontal_distance).toFixed(2)+' + 层间 '+Number(leg.vertical_distance).toFixed(2)+'）'+(leg.action?'\n'+(leg.action==='unload'?'卸货':'取货')+' · '+leg.owner_code+' · 载货 '+leg.load_before+' → '+leg.load_after+' 箱':'')).join('\n\n');
    }
    const all=document.createElement('option');all.value=-1;all.textContent='全部分段';$('route-select').append(all);
    legs.forEach((leg,index)=>{const option=document.createElement('option');option.value=index;option.textContent=(index+1)+'. '+leg.start+' → '+leg.end;$('route-select').append(option);});
    $('route-select').onchange=draw;draw();
  }catch(error){$('summary').textContent=error.message;}
})();
