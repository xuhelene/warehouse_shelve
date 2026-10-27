// 业务坐标采用 Z 向上；转换到 Three.js 的 Y 向上坐标。
(function () {
  'use strict';
  const config = { columnWidth: 1, rowDepth: 1.2, levelHeight: 1, frontY: 2,
    rows: [{id:'C14',x:-2.4,face:-1},{id:'C15',x:-1.2,face:1},{id:'C16',x:2.4,face:-1}],
    origin: {x:0,y:0,z:0} };
  const colors = {empty:0xd5e1eb,stock:0x488dcc,inbound:0x29bc88,outbound:0xf09840,selected:0xe658ae};
  const el = id => document.getElementById(id);
  const point = p => new THREE.Vector3(p.x,p.z || 0,p.y);
  const slots = new Map();
  let data = {inventory:[],targets:[],paths:[],mode:'inventory'}, selected = null;
  let scene, camera, renderer, controls, routeGroup, originMarker;
  function showSlotPopup(event) {
    const popup=el('slot-popup'),content=el('slot-popup-content');
    content.replaceChildren();
    const title=document.createElement('strong');title.textContent='货位号：'+selected;content.append(title);
    const item=data.inventory.find(row=>row.slot_id===selected);
    const lines=item?.details || [];
    if(lines.length) lines.forEach(row=>{
      const block=document.createElement('div');block.className='slot-popup-item';
      block.textContent='SKU编号：'+row.sku_id+'\n货物名称：'+row.name+
        '\n货物数量：'+row.quantity+(row.unit==='件'?'箱':row.unit)+' / '+row.base_quantity+row.base_unit+
        '\n入库时间：'+row.timestamp.slice(0,10);content.append(block);
    });
    else {
      const block=document.createElement('div');block.className='slot-popup-item';
      block.textContent=item?'货物：'+(item.item_id || '未提供')+'\n数量：'+(item.quantity ?? '未提供'):'空货位';content.append(block);
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
  function label(text,x,y,z,size=1.2) {
    const canvas = document.createElement('canvas'); canvas.width=512;canvas.height=96;
    const ctx=canvas.getContext('2d');ctx.font='36px Microsoft YaHei';ctx.textAlign='center';
    ctx.fillStyle='#ffffff';ctx.fillRect(0,0,512,96);ctx.fillStyle='#304860';ctx.fillText(text,256,60);
    const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(canvas)}));
    sprite.position.copy(point({x,y,z}));sprite.scale.set(size*4,size*.75,1);scene.add(sprite);
  }
  function setView(top) {
    controls.target.set(-.5,1,24);
    camera.position.set(top ? -.5 : -34,top ? 80 : 32,top ? 23.99 : -27);
    if(document.documentElement.classList.contains('embedded')) {
      camera.position.set(top ? -.5 : -20,top ? 38 : 20,top ? 23.99 : 24);
      camera.up.set(top ? 1 : 0,top ? 0 : 1,0);controls.update();return;
    }
    camera.up.set(0,1,0);controls.update();
  }
  function refresh() {
    const inventory=new Map(data.inventory.map(item=>[item.slot_id,item]));
    const targets=new Set(data.targets);
    for (const [id,mesh] of slots) {
      const state=id===selected?'selected':targets.has(id)?data.mode:inventory.has(id)?'stock':'empty';
      mesh.material.color.setHex(colors[state]);
      mesh.visible=mesh.userData.level<=Number(el('level').value);
      mesh.material.opacity=state==='empty'?.22:.92;
    }
    if(selected) {
      const item=inventory.get(selected),target=targets.has(selected);
      el('detail').textContent=selected+'\n状态：'+(target?(data.mode==='inbound'?'待入库':'待出库'):item?'有库存':'空货位')+
        (item?'\n货物：'+(item.item_id || '未提供')+'\n数量：'+(item.quantity ?? '未提供'):'')+
        '\n坐标：'+JSON.stringify(slots.get(selected).userData.coordinate);
    }
    el('summary').textContent='有效货位 '+slots.size+' · 库存货位 '+inventory.size+' · 目标货位 '+targets.size;
    while(routeGroup.children.length) {
      const child=routeGroup.children[0];routeGroup.remove(child);child.geometry.dispose();child.material.dispose();
    }
    data.paths.forEach(path=>{
      const points=path.map(p=>point({...p,z:.12}));
      routeGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),new THREE.LineBasicMaterial({color:colors[data.mode]})));
      for(let i=1;i<points.length;i++) {
        const vector=points[i].clone().sub(points[i-1]),length=vector.length();
        if(length>.4) {
          const arrow=new THREE.ArrowHelper(vector.normalize(),points[i-1].clone().lerp(points[i],.5),.65,colors[data.mode],.25,.2);
          // 使用普通线条存入同一组，便于释放绘图资源。
          arrow.updateMatrixWorld(true);
          for(const child of [arrow.line,arrow.cone]) {child.applyMatrix4(arrow.matrixWorld);routeGroup.add(child);}
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
    data={inventory,targets,paths,mode};selected=null;el('slot-popup').hidden=true;el('detail').textContent='点击货位查看编号与状态。';refresh();
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
    scene=new THREE.Scene();scene.background=new THREE.Color(0xeef2f6);
    camera=new THREE.PerspectiveCamera(42,1,.1,250);
    renderer=new THREE.WebGLRenderer({antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));el('canvas').appendChild(renderer.domElement);
    controls=new THREE.OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.maxDistance=130;controls.minDistance=3;
    controls.addEventListener('start',()=>{el('slot-popup').hidden=true;});
    el('close-slot-popup').onclick=()=>{el('slot-popup').hidden=true;};
    document.addEventListener('keydown',event=>{if(event.key==='Escape')el('slot-popup').hidden=true;});
    scene.add(new THREE.AmbientLight(0xffffff,.8));const light=new THREE.DirectionalLight(0xffffff,.6);light.position.set(-10,25,-10);scene.add(light);
    box(10,.08,50,-1.2,23.5,-.1,0xfafcfe);
    box(2.4,.025,45,-4.2,24.5,0,0xd5e8ed);box(2.4,.025,45,.6,24.5,0,0xd5e8ed);
    box(8.4,.03,3,-1.2,22.5,.01,0xafe1dc);box(8.4,.025,2,-1.2,1,0,0xd5e8ed);
    box(10,5.5,.15,-1.2,47.1,2.7,0xc8d2de);box(1.2,4.8,45,3.6,24.5,2.4,0xb6c0cd,.25);
    const shelfGeo=new THREE.BoxGeometry(1.16,.055,.99),postGeo=new THREE.BoxGeometry(.045,5,.045);
    const shelfMat=new THREE.MeshPhongMaterial({color:0xdf9d52}),postMat=new THREE.MeshPhongMaterial({color:0x516980});
    config.rows.forEach(row=>{
      for(let col=1;col<=45;col++) for(let level=1;level<=5;level++) {
        if(col>=20&&col<=22&&level<=2) continue;
        const y=config.frontY+(col-.5)*config.columnWidth,z=(level-.5)*config.levelHeight;
        const mesh=box(1.06,.74,.84,row.x,y,z,colors.empty,.22);
        const id=row.id+'-'+col+'-'+level;mesh.userData={slot_id:id,level,coordinate:{x:row.x,y,z}};slots.set(id,mesh);
        const shelf=new THREE.Mesh(shelfGeo,shelfMat);shelf.position.copy(point({x:row.x,y,z:level-1}));shelf.userData.level=level;scene.add(shelf);
      }
      for(let col=0;col<=45;col++) for(const dx of [-.57,.57]) {
        if(col===20||col===21) continue;
        const post=new THREE.Mesh(postGeo,postMat);post.position.copy(point({x:row.x+dx,y:2+col,z:2.5}));scene.add(post);
      }
      label(row.id,row.x,.9,2, .6);
    });
    label('门口 / 出货区',0,-.65,.15,.7);label('双向通道',-4.2,13,.2,.5);label('双向通道',.6,13,.2,.5);
    label('20—22 列贯通',-1.2,22.5,5.5,.7);label('第 45 列 · 靠墙',-1.2,47,6,.7);label('其他项目货架',3.6,35,5.5,.6);
    for(const col of [1,10,19,23,30,40,45]) label(String(col),-5.1,col+1.5,.2,.25);
    const axes=new THREE.AxesHelper(2);scene.add(axes);label('X 向右',2,0,.2,.35);label('Y 向墙',0,2,.2,.35);label('Z 向上',0,0,2.5,.35);
    routeGroup=new THREE.Group();scene.add(routeGroup);
    originMarker=new THREE.Mesh(new THREE.SphereGeometry(.18,16,12),new THREE.MeshPhongMaterial({color:0x23364b}));scene.add(originMarker);originMarker.position.copy(point({...config.origin,z:.2}));
    setView(false);demo('empty');
    new ResizeObserver(()=>{const host=el('canvas');camera.aspect=host.clientWidth/host.clientHeight;camera.updateProjectionMatrix();renderer.setSize(host.clientWidth,host.clientHeight);}).observe(el('canvas'));
    function animate(){requestAnimationFrame(animate);controls.update();renderer.render(scene,camera);}animate();
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
    el('level').onchange=()=>{el('slot-popup').hidden=true;refresh();scene.children.forEach(mesh=>{if(mesh.userData.level&&!mesh.userData.slot_id)mesh.visible=mesh.userData.level<=Number(el('level').value);});};
    el('perspective').onclick=()=>setView(false);el('top').onclick=()=>setView(true);
    el('locate').onclick=()=>{
      const id=el('slotId').value.trim().toUpperCase(),mesh=slots.get(id);
      if(!mesh){el('detail').textContent='货位不存在（20—22 列的第 1、2 层为通道）。';return;}
      selected=id;el('level').value='5';el('level').onchange();controls.target.copy(mesh.position);camera.position.copy(mesh.position).add(new THREE.Vector3(-5,5,-6));controls.update();
    };
    el('applyOrigin').onclick=()=>{
      const x=el('originX').valueAsNumber,y=el('originY').valueAsNumber;
      if(!Number.isFinite(x)||!Number.isFinite(y)||x < -5.4||x>3||y<0||y>=2){el('detail').textContent='起点范围：X 为 -5.4 到 3，Y 为 0 到小于 2。';return;}
      config.origin={x,y,z:0};originMarker.position.copy(point({x,y,z:.2}));demo(el('mode').value);
    };
    window.warehousePreview={config,setData,getSlots:()=>Array.from(slots.values()).map(mesh=>({...mesh.userData}))};
  } catch(error) {el('error').textContent='三维预览加载失败：'+error.message;}
})();
