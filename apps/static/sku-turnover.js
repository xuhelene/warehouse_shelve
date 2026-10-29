(() => {
  'use strict';
  const root=document.getElementById('turnover-settings')||document;
  const $=id=>root.querySelector('#'+id);
  function render(data){
    $('rows').replaceChildren();
    if(data && data.rows.some(row=>!row.owner_code || row.movement_boxes==null)){
      $('summary').textContent='当前结果为旧口径，请使用样例或上传完整订单历史重新计算入库+出库累计贡献等级。';return;
    }
    $('summary').textContent=data?`${data.start_date} 至 ${data.end_date} · ${data.outbound_rows} 条出库明细 · ${data.sku_count} 个仓库/货主/SKU组合 · 文件：${data.filename}`:'尚无结果，请使用样例或上传订单历史。';
    if(!data)return;
    for(const row of data.rows){
      const tr=document.createElement('tr');
      for(const key of ['warehouse_code','owner_code','sku_id','movement_boxes','rank','grade']){
        const td=document.createElement('td');td.textContent=row[key];td.style.padding='8px 20px 8px 0';tr.append(td);
      }
      $('rows').append(tr);
    }
  }
  async function request(url,options){
    root.querySelectorAll('button').forEach(button=>button.disabled=true);
    $('status').textContent='正在处理…';
    try{
      const response=await fetch(url,options), data=await response.json();
      if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'请求参数无效');
      render(data);$('status').textContent=options?'更新成功，输入和结果已保存。':'读取完成。';
    }catch(error){$('status').textContent='处理失败：'+error.message;}
    finally{root.querySelectorAll('button').forEach(button=>button.disabled=false);}
  }
  $('sample').onclick=()=>request('/api/sku-turnover/sample',{method:'POST'});
  $('update-form').onsubmit=event=>{
    event.preventDefault();const body=new FormData();body.append('file',$('file').files[0]);
    if($('end-date').value)body.append('end_date',$('end-date').value);
    request('/api/sku-turnover/update',{method:'POST',body});
  };
  request('/api/sku-turnover');
})();
