// 业务坐标采用 Z 向上；转换到 Three.js 的 Y 向上坐标。
(function () {
  'use strict';
  let layout, config;
  const colors = {empty:0xd5e1eb,stock:0x488dcc,inbound:0x29bc88,outbound:0x29bc88,selected:0xe658ae};
  const el = id => document.getElementById(id);
  const point = p => new THREE.Vector3(p.x,p.z || 0,p.y);
  const slots = new Map(), labels = [], targetMarkers = [];
  let fitTop = true;
  let data = {inventory:[],targets:[],paths:[],mode:'inventory'}, selected = null, rack = '', routeIndex = -1;
  let scene, camera, perspectiveCamera, topCamera, renderer, controls, routeGroup, originMarker;
  function showSlotPopup(event, ids=[selected]) {
    const popup=el('slot-popup'),content=el('slot-popup-content');
    content.replaceChildren();
    if(ids.length>1)ids.forEach(id=>{
      const button=document.createElement('button');button.className='target-slot-choice';button.textContent=id;
      button.onclick=()=>{selected=id;refresh();showSlotPopup(event,ids);};content.append(button);
    });
    const title=document.createElement('strong');title.textContent='货位号：'+selected;content.append(title);
    const rows=(data.targetDetails||[]).filter(row=>row.slot_id===selected);
    const remaining=data.inventory.find(row=>row.slot_id===selected);
    const quantityLabel=data.mode==='inbound'?'本次入库数量':'本次拣货数量';
    const item=rows.length?{details:rows}:data.inventory.find(row=>row.slot_id===selected);
    const lines=item?.details || [];
    if(lines.length) lines.forEach(row=>{
      const block=document.createElement('div');block.className='slot-popup-item';
      block.textContent='SKU编号：'+row.sku_id+'\n货物名称：'+row.name+
        '\n'+(rows.length?quantityLabel:'库存数量')+'：'+row.quantity+(row.unit==='件'?'箱':row.unit)+' / '+row.base_quantity+row.base_unit+
        '\n入库时间：'+(row.timestamp||'未提供').slice(0,10);content.append(block);
    });
    else {
      const block=document.createElement('div');block.className='slot-popup-item';
      block.textContent=item?'货物：'+(item.item_id || '未提供')+'\n'+(data.targets.length?'规划后剩余库存':'库存数量')+'：'+(item.quantity ?? '未提供'):'空货位';content.append(block);
    }
    if(data.targets.includes(selected)) {
      const block=document.createElement('div');block.className='slot-popup-item';
      block.textContent='规划后剩余库存：'+(remaining?.quantity||'0');content.append(block);
    }
    popup.hidden=false;
    const host=el('canvas').getBoundingClientRect();
    popup.style.left=Math.max(8,Math.min(event.clientX-host.left+12,host.width-popup.offsetWidth-8))+'px';
    popup.style.top=Math.max(8,Math.min(event.clientY-host.top+12,host.height-popup.offsetHeight-8))+'px';
  }
  function box(w,h,d,x,y,z,color,opacity=1) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(w,h,d),new THREE.MeshPhongMaterial({color,transparent:opacity<1,opacity}));
    mesh.position.copy(point({x,y,z})); scene.add(mesh); return mesh;
  }
  function label(text,x,y,z,size=1) {
    const canvas = document.createElement('canvas'),ctx=canvas.getContext('2d'),lines=text.split('\n');
    ctx.font='48px Microsoft YaHei';
    canvas.width=Math.ceil(Math.max(...lines.map(line=>ctx.measureText(line).width)))+24;canvas.height=72*lines.length;
    ctx.font='48px Microsoft YaHei';ctx.textAlign='center';ctx.textBaseline='middle';
    ctx.fillStyle='#304860';lines.forEach((line,index)=>ctx.fillText(line,canvas.width/2,36+72*index));
    const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(canvas),depthTest:false}));
    sprite.position.copy(point({x,y,z}));sprite.renderOrder=10;
    sprite.userData.labelAspect=canvas.width/canvas.height;sprite.userData.labelPixels=(size<.5?10:12)*lines.length;
    labels.push(sprite);scene.add(sprite);return sprite;
  }
  function setView(top) {
    fitTop=top;
    const nextCamera=top?topCamera:perspectiveCamera;
    if(camera!==nextCamera) {
      controls.dispose();camera=nextCamera;controls=new THREE.OrbitControls(camera,renderer.domElement);
      controls.enableDamping=true;controls.maxDistance=130;controls.minDistance=3;controls.minZoom=.3;controls.maxZoom=10;
      controls.addEventListener('start',()=>{fitTop=false;el('slot-popup').hidden=true;});
    }
    if(top) {
      const host=el('canvas'),width=Math.max(host.clientWidth,1),height=Math.max(host.clientHeight,1);
      const aspect=width/height;
      // 仓库长边沿画布横向展开，按可用宽高同时适配，并给标注留边距。
      const span=Math.max(52/aspect/(Math.max(width-64,1)/width),10.5/(Math.max(height-48,1)/height));
      camera.left=-span*aspect/2;camera.right=span*aspect/2;camera.top=span/2;camera.bottom=-span/2;camera.zoom=1;camera.updateProjectionMatrix();
      controls.target.set(-1,1,22);
      camera.position.set(-1,80,22);camera.up.set(1,0,0);
    } else {
      camera.aspect=Math.max(el('canvas').clientWidth,1)/Math.max(el('canvas').clientHeight,1);camera.updateProjectionMatrix();
      controls.target.set(-.5,1,24);
      camera.position.set(document.documentElement.classList.contains('embedded')?-20:-34,
        document.documentElement.classList.contains('embedded')?20:32,
        document.documentElement.classList.contains('embedded')?24:-27);
      camera.up.set(0,1,0);
    }
    controls.update();
  }
  function refresh() {
    const inventory=new Map(data.inventory.map(item=>[item.slot_id,item]));
    const targets=new Set(data.targets);
    for (const [id,mesh] of slots) {
      const state=id===selected?'selected':targets.has(id)?data.mode:inventory.has(id)?'stock':'empty';
      mesh.material.color.setHex(colors[state]);
      mesh.visible=mesh.userData.level<=Number(el('level').value)&&(!rack||mesh.userData.rack===rack);
      mesh.material.opacity=state==='empty'?.22:.92;
    }
    scene.children.forEach(mesh=>{
      if(mesh.userData.rack&&!mesh.userData.slot_id)mesh.visible=(!rack||mesh.userData.rack===rack)&&(!mesh.userData.level||mesh.userData.level<=Number(el('level').value));
    });
    if(selected) {
      const item=inventory.get(selected),target=targets.has(selected);
      el('detail').textContent=selected+'\n状态：'+(target?(data.mode==='inbound'?'待入库':'待出库'):item?'有库存':'空货位')+
        (target?'\n'+(data.mode==='inbound'?'本次入库数量':'本次拣货数量')+'：'+(data.targetDetails||[]).filter(row=>row.slot_id===selected).map(row=>row.base_quantity+row.base_unit).join('；'):'')+
        '\n'+(data.targets.length?'规划后剩余库存':'库存数量')+'：'+(item?.quantity??'0');
    }
    el('summary').textContent='有效货位 '+slots.size+' · 库存货位 '+inventory.size+' · 目标货位 '+targets.size;
    while(routeGroup.children.length) {
      const child=routeGroup.children[0];routeGroup.remove(child);child.geometry.dispose();child.material.dispose();
    }
    targetMarkers.forEach(marker=>{marker.element.remove();marker.leader.remove();});targetMarkers.length=0;
    const columns=new Map();
    for(const id of targets) {
      const mesh=slots.get(id);
      if(!mesh.visible)continue;
      const key=id.slice(0,id.lastIndexOf('-'));
      if(!columns.has(key))columns.set(key,[]);
      columns.get(key).push(id);
    }
    for(const ids of columns.values()) {
      ids.sort((a,b)=>slots.get(a).userData.level-slots.get(b).userData.level);
      const mesh=slots.get(ids[0]),element=document.createElement('button');
      element.className='target-marker';
      element.setAttribute('aria-label','推荐货位：'+ids.join('、'));element.title=ids.join('、');
      element.onclick=event=>{selected=ids[0];refresh();showSlotPopup(event,ids);};
      el('canvas').parentElement.append(element);
      const leader=document.createElementNS('http://www.w3.org/2000/svg','line');
      leader.setAttribute('stroke','#245ecc');leader.setAttribute('stroke-width','2.5');leader.setAttribute('stroke-linecap','round');el('target-leaders').append(leader);
      targetMarkers.push({element,leader,ids,coordinate:mesh.userData.coordinate,position:point({...mesh.userData.coordinate,z:Number(el('level').value)-.1})});
    }
    const visibleIds=new Set(Array.from(columns.values()).flat());
    const shownPaths=data.paths.filter((path,index)=>
      (routeIndex<0||index===routeIndex)&&(!data.targets.length||visibleIds.size>0)&&
      (!data.pathSlots?.[index]?.length||data.pathSlots[index].some(id=>visibleIds.has(id))));
    el('summary').textContent=data.targets.length?
      (visibleIds.size?'当前显示 '+visibleIds.size+' / '+targets.size+' 个目标货位 · '+shownPaths.length+' 段路线 · 蓝线为当前筛选相关路线':'当前筛选无目标货位，路线已隐藏'):
      '有效货位 '+slots.size+' · 库存货位 '+inventory.size;
    const routeTargets=new Set((data.pathSlots||[]).flatMap((ids,index)=>shownPaths.includes(data.paths[index])?ids:[]));
    for(const marker of targetMarkers) {
      const {coordinate,ids}=marker,lane=ids[0].startsWith('C14-')?-4.2:.6;
      marker.tickStart=point({x:lane,y:coordinate.y,z:.14});
      marker.tickEnd=point({x:lane+Math.sign(coordinate.x-lane)*.65,y:coordinate.y,z:.14});
      marker.showTick=shownPaths.length>0&&(!(data.pathSlots||[]).length||ids.some(id=>routeTargets.has(id)));
    }
    shownPaths.forEach(path=>{
      const points=path.map(p=>point({...p,z:.12}));
      let arrowDistance=0;
      for(let i=1;i<points.length;i++) {
        const vector=points[i].clone().sub(points[i-1]),length=vector.length();
        if(length>0) {
          const line=new THREE.Mesh(new THREE.CylinderGeometry(.045,.045,length,6),new THREE.MeshBasicMaterial({color:0x356fdb,depthTest:false,depthWrite:false}));
          line.position.copy(points[i-1]).lerp(points[i],.5);line.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),vector.clone().normalize());
          line.renderOrder=2;routeGroup.add(line);
        }
        arrowDistance+=length;
        if(length>.4&&(arrowDistance>=4||i===points.length-1)) {
          arrowDistance=0;
          const arrow=new THREE.ArrowHelper(vector.normalize(),points[i-1].clone().lerp(points[i],.5),.65,0x4c7cf5,.25,.2);
          // 使用普通线条存入同一组，便于释放绘图资源。
          arrow.updateMatrixWorld(true);
          for(const child of [arrow.line,arrow.cone]) {child.applyMatrix4(arrow.matrixWorld);child.material.depthTest=false;child.material.depthWrite=false;child.renderOrder=2;routeGroup.add(child);}
        }
      }
    });
  }
  // 后续业务页面可调用 setData({inventory:[{slot_id,item_id,quantity}],targets:[编号],mode:'inbound'|'outbound',paths:[[{x,y},...]]})。
  function setData(next) {
    const inventory=next.inventory || [],targets=next.targets || [],paths=next.paths || [],mode=next.mode || 'inventory';
    if(!Array.isArray(inventory)||!Array.isArray(targets)||!Array.isArray(paths)) throw new Error('库存、目标货位和路径须为数组');
    if(!['inventory','inbound','outbound'].includes(mode)) throw new Error('未知展示模式');
    for(const item of inventory) if(!item || !slots.has(item.slot_id)) throw new Error('无效库存货位：'+item?.slot_id);
    for(const id of targets) if(!slots.has(id)) throw new Error('无效目标货位：'+id);
    if(targets.length && mode==='inventory') throw new Error('目标货位需指定入库或出库模式');
    for(const path of paths) {
      if(!Array.isArray(path)||path.length<2||path.some(p=>!p||!Number.isFinite(p.x)||!Number.isFinite(p.y))) throw new Error('路径至少包含两个有效坐标点');
      for(let i=1;i<path.length;i++) {
        const a=path[i-1],b=path[i];
        if(a.x!==b.x&&a.y!==b.y) throw new Error('地面路径须沿通道正交行走');
        const steps=Math.ceil(Math.hypot(a.x-b.x,a.y-b.y)*20);
        for(let j=0;j<=steps;j++) {
          const t=steps?j/steps:0,x=a.x+(b.x-a.x)*t,y=a.y+(b.y-a.y)*t;
          if(x < -5.4 || x > 3 || y < 0 || y >= 47 || !(y<=2 || (y>=21&&y<=24) || x<=-3 || (x>=-.6&&x<=1.8))) throw new Error('路径超出通道或穿过货架');
        }
      }
    }
    originMarker.position.copy(point({...(paths[0]?.[0]||config.origin),z:.2}));
    routeIndex=-1;data={inventory,targets,paths,mode,targetDetails:next.targetDetails||[],pathSlots:next.pathSlots||[]};selected=null;el('slot-popup').hidden=true;el('detail').textContent='点击货位查看编号与状态。';refresh();
  }
  function demo(mode) {
    if(mode==='empty') return setData({});
    const inventory=Array.from(slots.keys()).filter((id,i)=>i%7===0).map((id,i)=>({slot_id:id,item_id:'演示货物-'+(i+1),quantity:10+i%5}));
    const inbound=mode==='inbound',outbound=mode==='outbound';
    const targets=inbound?['C14-10-3','C16-30-4']:outbound?['C14-10-3','C16-30-4']:[];
    const stock=inventory.filter(item=>!targets.includes(item.slot_id));
    if(outbound) targets.forEach(id=>stock.push({slot_id:id,item_id:'演示拣货商品',quantity:12}));
    const p=(x,y)=>({x,y}),o={...config.origin};
    // 固定示例经过两条纵向通道及中部贯通通道；不是路径优化算法。
    const paths=targets.length?[[o,p(-4.2,o.y),p(-4.2,11.5),p(-4.2,22.5),p(.6,22.5),p(.6,31.5),p(.6,o.y),o]]:[];
    setData({inventory:stock,targets,paths,mode:inbound?'inbound':outbound?'outbound':'inventory'});
  }
  try {
    layout=JSON.parse(el('warehouse-layout').textContent);
    if(layout.schema_version!==1||layout.warehouse_type!=='rack'||!Array.isArray(layout.slots)||!layout.visualization)throw new Error('无效的立体库布局 JSON');
    config=layout.visualization.config;
    // 兼容浏览器缓存的旧预览 HTML，缺少连线层也不能阻断三维初始化。
    if(!el('target-leaders')) {
      const overlay=document.createElementNS('http://www.w3.org/2000/svg','svg');
      overlay.id='target-leaders';overlay.setAttribute('aria-hidden','true');
      Object.assign(overlay.style,{position:'absolute',inset:'0',width:'100%',height:'100%',pointerEvents:'none',zIndex:'2'});
      el('canvas').parentElement.append(overlay);
    }
    scene=new THREE.Scene();scene.background=new THREE.Color(0xeef2f6);
    perspectiveCamera=new THREE.PerspectiveCamera(42,1,.1,250);
    topCamera=new THREE.OrthographicCamera(-30,30,10,-10,.1,250);camera=perspectiveCamera;
    renderer=new THREE.WebGLRenderer({antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));el('canvas').appendChild(renderer.domElement);
    controls=new THREE.OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.maxDistance=130;controls.minDistance=3;
    controls.addEventListener('start',()=>{fitTop=false;el('slot-popup').hidden=true;});
    el('close-slot-popup').onclick=()=>{el('slot-popup').hidden=true;};
    document.addEventListener('keydown',event=>{if(event.key==='Escape')el('slot-popup').hidden=true;});
    scene.add(new THREE.AmbientLight(0xffffff,.8));const light=new THREE.DirectionalLight(0xffffff,.6);light.position.set(-10,25,-10);scene.add(light);
    const slotDefinitions=new Map(layout.slots.map(slot=>[slot.slot_id,slot]));
    for(const item of layout.visualization.objects) {
      const p=item.position;
      let object;
      if(item.type==='box') {
        const s=item.size;
        object=box(s.width,s.height,s.depth,p.x,p.y,p.z,item.color,item.opacity??1);
      } else if(item.type==='label') {
        object=label(item.text,p.x,p.y,p.z,item.scale);
        if(item.center)object.center.set(...item.center);
      } else if(item.type==='arrow') {
        object=new THREE.ArrowHelper(point(item.direction),point(p),item.length,item.color,item.head_length,item.head_width);
        scene.add(object);
      } else throw new Error('未知布局对象：'+item.type);
      Object.assign(object.userData,item.metadata||{});
      if(item.slot_id) {
        const slot=slotDefinitions.get(item.slot_id);
        if(!slot||slots.has(item.slot_id))throw new Error('无效或重复货位：'+item.slot_id);
        object.userData={slot_id:slot.slot_id,level:slot.level,rack:slot.rack,coordinate:p};slots.set(slot.slot_id,object);
      }
    }
    if(slots.size!==layout.slots.length)throw new Error('货位数据与布局图不一致');
    const axes=new THREE.AxesHelper(2);scene.add(axes);
    routeGroup=new THREE.Group();scene.add(routeGroup);
    originMarker=new THREE.Mesh(new THREE.SphereGeometry(.18,16,12),new THREE.MeshPhongMaterial({color:0x23364b}));scene.add(originMarker);originMarker.position.copy(point({...config.origin,z:.2}));
    setView(true);demo('empty');
    new ResizeObserver(()=>{const host=el('canvas');camera.aspect=host.clientWidth/host.clientHeight;camera.updateProjectionMatrix();renderer.setSize(host.clientWidth,host.clientHeight);if(fitTop)setView(true);}).observe(el('canvas'));
    function animate(){requestAnimationFrame(animate);controls.update();
      for(const sprite of labels) {
        const scale=(camera.isOrthographicCamera?(camera.top-camera.bottom)/camera.zoom:2*camera.position.distanceTo(sprite.position)*Math.tan(camera.fov*Math.PI/360))/Math.max(el('canvas').clientHeight,1);
        const height=sprite.userData.labelPixels*1.5*scale;
        sprite.scale.set(height*sprite.userData.labelAspect,height,1);
      }
      const host=el('canvas');
      el('target-leaders').style.height=host.clientHeight+'px';
      el('target-leaders').setAttribute('viewBox','0 0 '+host.clientWidth+' '+host.clientHeight);
      const screen=p=>{const v=p.clone().project(camera);return {x:(v.x+1)*host.clientWidth/2,y:(1-v.y)*host.clientHeight/2,z:v.z};};
      for(const marker of targetMarkers) {
        const p=screen(marker.position);
        marker.element.hidden=p.z < -1||p.z > 1||p.x<0||p.x>host.clientWidth||p.y<0||p.y>host.clientHeight;
        const px=screen(marker.position.clone().add(new THREE.Vector3(.34,0,0)));
        const py=screen(marker.position.clone().add(new THREE.Vector3(0,0,.34)));
        const diameter=2*Math.min(Math.hypot(px.x-p.x,px.y-p.y),Math.hypot(py.x-p.x,py.y-p.y));
        Object.assign(marker.element.style,{left:p.x+'px',top:p.y+'px',width:diameter+'px',height:diameter+'px'});
        marker.leader.style.display=marker.showTick?'':'none';
        if(marker.showTick) {
          const a=screen(marker.tickStart),b=screen(marker.tickEnd);
          marker.leader.setAttribute('x1',a.x);marker.leader.setAttribute('y1',a.y);
          marker.leader.setAttribute('x2',b.x);marker.leader.setAttribute('y2',b.y);
        }
      }
      renderer.render(scene,camera);}animate();
    const ray=new THREE.Raycaster(),mouse=new THREE.Vector2();let down;
    renderer.domElement.addEventListener('pointerdown',event=>{down={x:event.clientX,y:event.clientY};});
    renderer.domElement.addEventListener('pointerup',event=>{
      if(!down||Math.hypot(event.clientX-down.x,event.clientY-down.y)>5)return;
      const rect=renderer.domElement.getBoundingClientRect();mouse.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);
      ray.setFromCamera(mouse,camera);const hit=ray.intersectObjects(Array.from(slots.values()).filter(mesh=>mesh.visible))[0];
      if(hit){selected=hit.object.userData.slot_id;el('slotId').value=selected;refresh();showSlotPopup(event);}
      else el('slot-popup').hidden=true;
    });
    el('mode').onchange=()=>demo(el('mode').value);
    el('level').onchange=()=>{el('slot-popup').hidden=true;refresh();};
    el('perspective').onclick=()=>setView(false);el('top').onclick=()=>setView(true);
    el('locate').onclick=()=>{
      const id=el('slotId').value.trim().toUpperCase(),mesh=slots.get(id);
      if(!mesh){el('detail').textContent='货位不存在（20—22 列的第 1、2 层为通道）。';return;}
      selected=id;rack='';el('level').value='5';el('level').onchange();controls.target.copy(mesh.position);camera.position.copy(mesh.position).add(new THREE.Vector3(-5,5,-6));controls.update();
    };
    el('applyOrigin').onclick=()=>{
      const x=el('originX').valueAsNumber,y=el('originY').valueAsNumber;
      if(!Number.isFinite(x)||!Number.isFinite(y)||x < -5.4||x>3||y<0||y>=2){el('detail').textContent='起点范围：X 为 -5.4 到 3，Y 为 0 到小于 2。';return;}
      config.origin={x,y,z:0};originMarker.position.copy(point({x,y,z:.2}));demo(el('mode').value);
    };
    window.warehousePreview={config,setData,setRoute:index=>{routeIndex=Number(index);refresh();},setRack:value=>{rack=value;selected=null;el('slot-popup').hidden=true;el('detail').textContent='点击货位查看编号与状态。';refresh();},getSlots:()=>Array.from(slots.values()).map(mesh=>({...mesh.userData}))};
  } catch(error) {el('error').textContent='三维预览加载失败：'+error.message;}
})();
