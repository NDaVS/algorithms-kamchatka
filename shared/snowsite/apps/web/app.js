import {nextId,createRequestGate,validateTruckField,aggregateTrips,isComparable,headline,shortSiteName,fmtKm,fmtRub,fmtDuration} from './lib/core.js';
const $=id=>document.getElementById(id);
const gate=createRequestGate();
let network,scenario,map,roadLayer,pointLayer,routeLayer,edgeLookup,lastResult=null,variants=[],selectedKey='base',selectedMode=null,hasFit=false;
const markers=new Map();
const ROUTE_COLOR='#10243f',BACK_COLOR='#a259d9';
const pl=(n,f)=>{const a=Math.abs(n)%100,b=a%10;return a>10&&a<20?f[2]:b>1&&b<5?f[1]:b===1?f[0]:f[2];};
const trips=n=>n+' '+pl(n,['рейс','рейса','рейсов']);
const safe2=s=>String(s??'').replace(/[^\p{L}\p{N}]/gu,'').slice(0,2);
const el=(tag,text,cls)=>{const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;};
async function getJson(path,options){const r=await fetch(path,options);const v=await r.json();if(!r.ok)throw Error(v.error||'Не удалось выполнить запрос');return v;}
function msg(id,s){const n=$(id);if(n)n.textContent=s;}
function latLng(nodeId){const xy=network.nodes[nodeId];return [xy[1],xy[0]];}

function nearestNode(latlng){let best=null,score=Infinity;for(const [id,xy] of Object.entries(network.nodes)){const dx=(xy[0]-latlng.lng)*Math.cos(latlng.lat*Math.PI/180),dy=xy[1]-latlng.lat,d=dx*dx+dy*dy;if(d<score){score=d;best=id;}}return {nodeId:best,approxMeters:Math.sqrt(score)*111000};}
// Relief: 10x10 Terrarium tiles at z11 (2560 px); same bounds as the Sever basemap.
const RELIEF_BOUNDS=[[662,1922],[672,1932]].map(([y,x])=>[Math.atan(Math.sinh(Math.PI*(1-2*y/2048)))*180/Math.PI,x/2048*360-180]);
// Vector detail (coast, water, forest, urban, buildings, context roads) from the same OSM snapshot; loaded lazily.
function addStreetDetail(map){
  map.createPane('geography').style.zIndex='320';map.getPane('geography').style.pointerEvents='none';
  const renderer=L.canvas({pane:'geography',padding:.2}),group=L.layerGroup().addTo(map);
  const colors={building:'#a9a99f',forest:'#bbceb6',park:'#c6d9bb',brush:'#d0d9c1',urban:'#e8e6dd',industrial:'#dad7ce',water:'#aacacd',river:'#81afb4',coast:'#73a7ac'};
  let ctx=null;
  function refresh(){
    group.clearLayers();if(!ctx||map.getZoom()<11.5)return;
    const rect=map.getBounds().pad(.1),ids=new Set();
    for(let x=Math.floor(rect.getWest()*100);x<=Math.floor(rect.getEast()*100);x++)for(let y=Math.floor(rect.getSouth()*100);y<=Math.floor(rect.getNorth()*100);y++)for(const id of ctx.cells[x+'/'+y]||[])ids.add(id);
    for(const id of [...ids].sort((a,b)=>a-b)){
      const [kind,rings]=ctx.records[id];if(kind==='building'&&map.getZoom()<14.5)continue;
      const lines=rings.map(ring=>{const line=[];let x=0,y=0;for(let i=0;i<ring.length;i+=2){x+=ring[i];y+=ring[i+1];line.push([y/100000,x/100000]);}return line;});
      const style={pane:'geography',renderer,interactive:false,color:colors[kind],weight:1,fillOpacity:kind==='building'?.76:kind==='water'?1:.40,opacity:kind==='coast'?.75:.45};
      if(kind==='contextRoad')L.polyline(lines,{...style,color:'#718087',weight:map.getZoom()<14?1:2,opacity:.65}).addTo(group);
      else if(kind==='coast'||kind==='river')L.polyline(lines,style).addTo(group);
      else L.polygon(lines,style).addTo(group);
    }
  }
  map.on('moveend',refresh);
  import('/city-context.js').then(m=>{ctx=m.cityContext;refresh();}).catch(()=>{});
}
function sanitizeScenario(raw){
  if(!raw||typeof raw!=='object'||!Array.isArray(raw.sites)||!Array.isArray(raw.demands))throw Error('Неверная структура JSON');
  if(raw.demands.length>50||raw.sites.length>30)throw Error('Слишком много точек (до 50 источников и 30 площадок)');
  const ids=new Set(),str=(v,d)=>typeof v==='string'&&v.trim()?v.trim().slice(0,120):d;
  const chk=(x,g)=>{if(!x||typeof x.id!=='string'||!x.id.trim()||ids.has(x.id))throw Error('ID точек должны быть уникальными');ids.add(x.id);
    if(!Object.hasOwn(network.nodes,String(x.nodeId)))throw Error('Точка '+x.id+' не привязана к узлу текущей дорожной сети');
    if(g==='d'&&!(Number.isSafeInteger(x.volumeM3)&&x.volumeM3>0))throw Error('Объём источника '+x.id+' должен быть целым положительным');
    if(g==='s'&&!(Number.isSafeInteger(x.capacityM3)&&x.capacityM3>=0))throw Error('Вместимость площадки '+x.id+' должна быть целой неотрицательной');
    if(g==='s'&&!['existing','candidate'].includes(x.kind))throw Error('Тип площадки '+x.id+' должен быть existing или candidate');};
  raw.demands.forEach(x=>chk(x,'d'));raw.sites.forEach(x=>chk(x,'s'));
  const t=raw.truck||{};
  if(!(Number.isSafeInteger(t.bodyM3)&&t.bodyM3>0)||!(Number.isFinite(t.driveKmh)&&t.driveKmh>0))throw Error('Некорректные параметры грузовика');
  return {id:str(raw.id,'imported'),name:str(raw.name,'Импортированный сценарий'),mode:'scenario_only',provenance:str(raw.provenance,'Импорт пользователя; не подтверждено'),
    truck:{bodyM3:t.bodyM3,payloadKg:t.payloadKg??null,driveKmh:t.driveKmh,rateRubPerHour:t.rateRubPerHour??null},snowDensityKgM3:raw.snowDensityKgM3??null,
    closedEdgeIds:Array.isArray(raw.closedEdgeIds)?raw.closedEdgeIds.map(String):[],
    demands:raw.demands.map(x=>({id:x.id,name:str(x.name,x.id),nodeId:String(x.nodeId),volumeM3:x.volumeM3})),
    sites:raw.sites.map(x=>({id:x.id,name:str(x.name,x.id),nodeId:String(x.nodeId),capacityM3:x.capacityM3,kind:x.kind,legalStatus:'unverified'}))};
}
function initMap(){
  map=L.map('map',{preferCanvas:true,zoomControl:false,minZoom:9,maxZoom:19,zoomSnap:.25,attributionControl:false,renderer:L.canvas({tolerance:3,padding:.15})}).setView([53.05,158.63],12.5);
  map.setMaxBounds(L.latLngBounds(RELIEF_BOUNDS));
  L.control.zoom({position:'topright'}).addTo(map);
  L.imageOverlay('/terrain-map.webp',RELIEF_BOUNDS,{pane:'tilePane',interactive:false,opacity:1,alt:'Рельеф Авачинской губы'}).addTo(map);
  addStreetDetail(map);
  roadLayer=L.polyline(network.edges.map(e=>e.geometry.map(([lon,lat])=>[lat,lon])),{color:'#7f9195',weight:1.1,opacity:.9,interactive:false,renderer:L.canvas({tolerance:3})}).addTo(map);
  map.createPane('routes').style.zIndex='450';
  routeLayer=L.layerGroup().addTo(map);pointLayer=L.layerGroup().addTo(map);
  map.on('click',e=>{
    if(!selectedMode)return;
    const point=nearestNode(e.latlng);
    if(point.approxMeters>1500){msg('pick-help','Точка далеко от дороги: выберите ближе к дороге.');$('pick-banner').textContent='Слишком далеко от дороги — выберите ближе';return;}
    const site=selectedMode==='site',ids=[...scenario.demands.map(x=>x.id),...scenario.sites.map(x=>x.id)];
    const id=nextId(site?'CUSER':'DUSER',ids),n=Number(id.replace(/\D+/g,''))||1;
    if(site)scenario.sites.push({id,name:'Кандидат '+n+' — сценарий',nodeId:point.nodeId,capacityM3:100,kind:'candidate',legalStatus:'unverified'});
    else scenario.demands.push({id,name:'Источник снега '+n+' — сценарий',nodeId:point.nodeId,volumeM3:30});
    endPick();changed();markers.get(id)?.openPopup();msg('stats-status','Точка добавлена — нажмите «Пересчитать»');
  });
}
// ---------- points on the map ----------
function mkIcon(kind,label,cls=''){const s=kind==='cand'?36:28;return L.divIcon({className:`pt pt-${kind} ${cls}`,html:`<span>${safe2(label)}</span>`,iconSize:[s,s],iconAnchor:[s/2,s/2]});}
const curVariant=()=>variants.find(v=>v.key===selectedKey)||null;
function servedMap(v){const m=new Map();if(v)for(const a of v.res.assignments)m.set(a.demandId,(m.get(a.demandId)||0)+a.volumeM3);return m;}
function sourcePopup(d,i,v){
  const box=el('div');box.append(el('h3',`Источник ${i+1}: ${d.name}`),el('p',`Нужно вывезти: ${d.volumeM3} м³`));
  if(!v){box.append(el('p','Расчёт ещё не выполнен.'));return box;}
  const per=new Map();for(const a of v.res.assignments.filter(a=>a.demandId===d.id)){const x=per.get(a.siteId)||{t:0,m:0};x.t++;x.m+=a.volumeM3;per.set(a.siteId,x);}
  const served=[...per.values()].reduce((s,x)=>s+x.m,0),rest=d.volumeM3-served;
  box.append(el('p',`Вывезено: ${served} м³ · Осталось: ${rest} м³`));
  for(const [sid,x] of per){const s=scenario.sites.find(z=>z.id===sid);box.append(el('p',`→ ${s?shortSiteName(s):sid}: ${trips(x.t)}, ${x.m} м³`));}
  if(rest>0){const r=(v.res.unservedReasons||[]).find(z=>z.demandId===d.id);box.append(el('p',(r?r.message:'Вместимость исчерпана')+'.','warn-text'));}
  return box;
}
function sitePopup(x,v){
  const box=el('div');box.append(el('h3',x.name),el('p',x.kind==='existing'?'Условная действующая площадка (сценарий)':'Рассматриваемая площадка — не утверждена'),el('p',`Вместимость (сценарная): ${x.capacityM3} м³`));
  if(v){const u=v.res.siteUtilization.find(z=>z.siteId===x.id);
    if(u){const n=v.res.assignments.filter(a=>a.siteId===x.id).length;box.append(el('p',`В этом варианте занято: ${u.usedM3} из ${u.capacityM3} м³ · осталось ${u.capacityM3-u.usedM3} м³`),el('p',`Рейсов: ${n}`));}
    else box.append(el('p','Не участвует в выбранном варианте.'));}
  box.append(el('p','Правовой статус: не проверен'));return box;
}
function renderPoints(){
  pointLayer.clearLayers();markers.clear();
  const v=curVariant(),active=v?new Set(v.siteIds):null,served=servedMap(v);
  scenario.demands.forEach((d,i)=>{if(!network.nodes[d.nodeId])return;
    const short=v&&d.volumeM3-(served.get(d.id)||0)>0;
    markers.set(d.id,L.marker(latLng(d.nodeId),{icon:mkIcon('src',String(i+1),short?'pt-short':''),title:`Источник ${i+1}: ${d.name}`,riseOnHover:true}).bindPopup(()=>sourcePopup(d,i,curVariant())).addTo(pointLayer));});
  for(const x of scenario.sites){if(!network.nodes[x.nodeId])continue;
    const off=active&&!active.has(x.id),kind=x.kind==='existing'?'base':'cand';
    markers.set(x.id,L.marker(latLng(x.nodeId),{icon:mkIcon(kind,shortSiteName(x),off?'pt-off':''),title:x.name+(off?' (не участвует в выбранном варианте)':''),riseOnHover:true,zIndexOffset:off?-500:200}).bindPopup(()=>sitePopup(x,curVariant())).addTo(pointLayer));}
  const total=scenario.demands.reduce((s,d)=>s+d.volumeM3,0),nb=scenario.sites.filter(x=>x.kind==='existing').length,nc=scenario.sites.length-nb;
  msg('task',`Сравниваем варианты вывоза снега: ${scenario.demands.length} ${pl(scenario.demands.length,['источник','источника','источников'])} (${total} м³), ${nb} условных действующих площадок и ${nc} ${pl(nc,['место-кандидат','места-кандидата','мест-кандидатов'])} — кандидаты проверяются по одному.`);
}
// ---------- routes ----------
function pathLatLngs(path){const out=[];for(const arc of path.arcs){const e=edgeLookup.get(arc.edgeId);if(!e)continue;let g=e.geometry;if(!arc.forward)g=[...g].reverse();for(const [lon,lat] of g)out.push([lat,lon]);}return out;}
function routePopup(g,loaded,i,s){const box=el('div');const km=loaded?g.outwardKm:g.homeKm;
  box.append(el('h3',loaded?`Гружёный: источник ${i+1} → площадка ${s}`:`Возврат пустой машины: площадка ${s} → источник ${i+1}`),el('p',`Рейсов: ${g.trips} · объём: ${g.volumeM3} м³`),el('p',`Длина в одну сторону: ${fmtKm(km)} км · за все рейсы (в одну сторону): ${fmtKm(km*g.trips)} км`));return box;}
function drawRoutes(v){
  routeLayer.clearLayers();if(!v)return;
  for(const g of aggregateTrips(v.res.assignments)){
    const i=scenario.demands.findIndex(d=>d.id===g.demandId),site=scenario.sites.find(x=>x.id===g.siteId),s=site?shortSiteName(site):g.siteId;
    const out=pathLatLngs(g.outward),back=pathLatLngs(g.home);if(!out.length&&!back.length)continue;
    if(out.length)L.polyline(out,{pane:'routes',color:'#fff',weight:8,opacity:.8,interactive:false}).addTo(routeLayer);
    if(back.length)L.polyline(back,{pane:'routes',color:BACK_COLOR,weight:6,opacity:.95,dashArray:'8 8',bubblingMouseEvents:false}).bindPopup(()=>routePopup(g,false,i,s)).addTo(routeLayer);
    if(out.length){L.polyline(out,{pane:'routes',color:ROUTE_COLOR,weight:3,opacity:1,bubblingMouseEvents:false}).bindPopup(()=>routePopup(g,true,i,s)).addTo(routeLayer);
      L.marker(out[Math.floor(out.length/2)],{pane:'routes',interactive:false,keyboard:false,icon:L.divIcon({className:'route-label',html:`<span>${g.trips}</span>`,iconSize:[24,24],iconAnchor:[12,12]})}).addTo(routeLayer);}
  }
}
function fitMap(){const pts=[...scenario.demands,...scenario.sites].filter(x=>network.nodes[x.nodeId]).map(x=>latLng(x.nodeId));if(!pts.length)return;
  const lg=document.querySelector('.legend'),room=lg&&lg.open?lg.offsetHeight+24:30;   // keep points clear of the legend
  map.fitBounds(L.latLngBounds(pts),{maxZoom:13.5,paddingTopLeft:[60,60],paddingBottomRight:[60,room]});}
// ---------- result panel ----------
function buildVariants(r){
  const baseIds=scenario.sites.filter(x=>x.kind==='existing').map(x=>x.id),sn=id=>shortSiteName(scenario.sites.find(x=>x.id===id)||{id});
  const baseLabel=baseIds.map(sn).join(' + ');
  return [{key:'base',label:'База',comp:baseLabel,res:r.baseline,siteIds:baseIds,isBase:true},
    ...r.variants.map(v=>({key:v.siteId,label:'+ '+sn(v.siteId),comp:baseLabel+' + '+sn(v.siteId),res:v,siteIds:[...baseIds,v.siteId],isBase:false}))];
}
function kpi(label,value,small,cls){const d=el('div',null,'kpi'+(cls?' '+cls:''));d.append(el('span',label),el('strong',value));if(small)d.append(el('small',small));return d;}
function renderResult(){
  const vbox=$('variants'),v=curVariant();vbox.replaceChildren();
  for(const x of ['composition','headline','kpis','sites-use','routes-text','details-body'])$(x).replaceChildren();
  $('headline').className='headline';
  if(!lastResult||!v){
    $('composition').textContent='';$('headline').append(el('span','Условия изменены или расчёт ещё не выполнен. Нажмите «Пересчитать».','empty'));
    drawRoutes(null);return;
  }
  const base=variants[0].res,total=scenario.demands.reduce((s,d)=>s+d.volumeM3,0);
  for(const x of variants){const b=el('button',null);b.type='button';b.setAttribute('aria-pressed',String(x.key===selectedKey));b.dataset.key=x.key;b.append(el('b',x.label),el('small',x.isBase?'без нового места':'площадки '+x.comp));b.addEventListener('click',()=>{selectedKey=x.key;renderPoints();renderResult();});vbox.append(b);}
  $('composition').textContent=v.isBase?`База = ${v.comp}. Новых площадок нет.`:`Вариант ${v.label} = ${v.comp}. Добавляется одна новая площадка, остальные кандидаты не учитываются.`;
  const h=headline(total,base,v.res,v.isBase);$('headline').className='headline '+h.tone;$('headline').textContent=h.text;
  const res=v.res,cmp=!v.isBase&&isComparable(base,res);
  const k=$('kpis');
  k.append(kpi('Вывезено',`${res.servedM3} из ${total} м³`),kpi('Осталось',`${res.unservedM3} м³`,res.unservedM3>0?'требует внимания':'всё вывезено',res.unservedM3>0?'bad':''),
    kpi('Пробег туда и обратно',fmtKm(res.transportKm)+' км',v.isBase?null:cmp?`${res.transportKm-base.transportKm<=0?'−':'+'}${fmtKm(Math.abs(res.transportKm-base.transportKm))} км к базе`:'не сравнимо с базой: другой объём вывоза'),
    kpi('Рейсов',String(res.trips)));
  const det=el('details',null,'more');det.style.gridColumn='1/3';det.append(el('summary','Время и затраты движения'));
  const g=el('div',null,'kpis');g.append(kpi('Время в дороге',fmtDuration(res.estimatedDriveMinutes)),kpi('Условные затраты',res.estimatedCostRub==null?'тариф не задан':fmtRub(res.estimatedCostRub),'только движение; без погрузки, разгрузки и содержания площадок'));det.append(g);k.append(det);
  const su=$('sites-use');su.append(el('h3','Загрузка площадок'));
  const active=new Set(v.siteIds);
  for(const s of scenario.sites){const u=res.siteUtilization.find(z=>z.siteId===s.id),row=el('div',null,'use-row'+(active.has(s.id)?'':' off'));
    row.append(el('span',`${shortSiteName(s)} — ${s.kind==='existing'?'действующая (условно)':'кандидат'}`),el('span',active.has(s.id)?`${u?u.usedM3:0} / ${s.capacityM3} м³`:'не участвует'));
    if(active.has(s.id)){const p=document.createElement('progress');p.max=Math.max(1,s.capacityM3);p.value=u?u.usedM3:0;p.setAttribute('aria-label',`Загрузка площадки ${shortSiteName(s)}`);row.append(p);}su.append(row);}
  const rt=$('routes-text');
  for(const a of aggregateTrips(res.assignments)){const i=scenario.demands.findIndex(d=>d.id===a.demandId),s=scenario.sites.find(x=>x.id===a.siteId);
    rt.append(el('li',`Источник ${i+1} → площадка ${s?shortSiteName(s):a.siteId}: ${trips(a.trips)}, ${a.volumeM3} м³; ${fmtKm(a.outwardKm+a.homeKm)} км за рейс туда и обратно`));}
  if(!rt.children.length)rt.append(el('li','Рейсов нет.'));
  const db=$('details-body');
  if(res.unservedReasons?.length){db.append(el('h3','Почему остался снег','small-h'));const ul=el('ul',null,'reasons');
    for(const u of res.unservedReasons){const i=scenario.demands.findIndex(d=>d.id===u.demandId);ul.append(el('li',`Источник ${i+1} (${u.demandId}): не вывезено ${u.unservedM3} м³ — ${u.message} [${u.code}]`));}db.append(ul);}
  const t=document.createElement('table');t.className='cmp';const cap=t.createCaption();cap.textContent='Сравнение вариантов';cap.className='small-h';
  const hr=t.createTHead().insertRow();for(const x of ['Вариант','Вывезено, м³','Осталось, м³','Пробег, км','Рейсов','Сравнимо с базой'])hr.append(Object.assign(document.createElement('th'),{textContent:x,scope:'col'}));
  const tb=t.createTBody();for(const x of variants){const r=tb.insertRow();const th=document.createElement('th');th.scope='row';th.textContent=x.isBase?'База':x.label;r.append(th);
    for(const c of [x.res.servedM3,x.res.unservedM3,fmtKm(x.res.transportKm),x.res.trips,x.isBase?'—':isComparable(base,x.res)?'да':'нет (другой объём)'])r.insertCell().textContent=String(c);}
  db.append(t);
  drawRoutes(v);
}
function changed(){gate.changed();lastResult=null;variants=[];renderPoints();renderResult();renderEntries();msg('stats-status','Изменено — пересчитайте');const b=$('compare');b.textContent='Пересчитать';}
const invalidate=changed;
async function compare(){
  if(!updateTruck()){msg('stats-status','Исправьте выделенные поля («Изменить условия» → «Машина»)');return;}
  const token=gate.begin(),button=$('compare');button.textContent='Считаем…';msg('stats-status','Считаем…');
  const snapshot=JSON.stringify(scenario);
  try{
    const r=await getJson('/api/compare',{method:'POST',headers:{'Content-Type':'application/json'},body:snapshot,signal:token.signal});
    if(!gate.isCurrent(token))return;
    lastResult=r;variants=buildVariants(r);
    if(!variants.some(x=>x.key===selectedKey)||selectedKey==='base'&&!hasFit)selectedKey=variants[1]?.key||'base';
    renderPoints();renderResult();if(!hasFit){fitMap();hasFit=true;}msg('stats-status','Готово');
  }catch(err){
    if(err.name==='AbortError'||!gate.isCurrent(token))return;
    msg('stats-status','Ошибка');lastResult=null;variants=[];renderResult();$('headline').replaceChildren(el('span','Не удалось рассчитать: '+err.message,'empty'));
  }finally{if(gate.isCurrent(token)||!gate.busy){gate.finish(token);button.textContent='Пересчитать';}}
}
// ---------- form (single source of truth) ----------
const TRUCK_INPUTS=[['bodyM3','body'],['payloadKg','payload'],['driveKmh','speed'],['rateRubPerHour','rate'],['snowDensityKgM3','density']];
function fieldError(inputId,text){let e=document.getElementById(inputId+'-err');const input=$(inputId);
  if(!e){e=el('small',null,'field-err');e.id=inputId+'-err';e.setAttribute('role','alert');input.after(e);}
  e.textContent=text||'';input.setAttribute('aria-invalid',text?'true':'false');}
function onTruckInput(field,inputId){const r=validateTruckField(field,$(inputId).value);
  if(!r.ok){fieldError(inputId,r.error);return false;}
  fieldError(inputId,'');if(field==='snowDensityKgM3')scenario.snowDensityKgM3=r.value;else scenario.truck[field]=r.value;return true;}
function updateTruck(){let ok=true;for(const [f,id] of TRUCK_INPUTS)ok=onTruckInput(f,id)&&ok;return ok;}
function truckControls(){const t=scenario.truck;$('body').value=t.bodyM3??'';$('payload').value=t.payloadKg??'';$('density').value=scenario.snowDensityKgM3??'';$('speed').value=t.driveKmh??'';$('rate').value=t.rateRubPerHour??'';for(const [,id] of TRUCK_INPUTS)fieldError(id,'');}
// ---------- drawer: points list ----------
function renderEntries(){const div=$('entries');div.replaceChildren();
  const rows=[...scenario.demands.map((p,i)=>({p,kind:'demand',i})),...scenario.sites.map(p=>({p,kind:'site'}))];
  for(const {p:point,kind,i} of rows){
    const array=kind==='demand'?scenario.demands:scenario.sites,entry=el('div',null,'entry'),title=el('div');
    if(kind==='site'&&point.kind==='candidate'){const nm=document.createElement('input');nm.type='text';nm.maxLength=120;nm.value=point.name;nm.className='name-edit';nm.setAttribute('aria-label','Название кандидата');
      nm.addEventListener('change',()=>{const v=nm.value.trim();if(v){point.name=v.slice(0,120);changed();}else nm.value=point.name;});title.append(nm);}
    else title.append(el('strong',kind==='demand'?`${i+1}. ${point.name}`:point.name));
    title.append(el('span',kind==='demand'?'Источник · объём, м³':point.kind==='existing'?'Условная действующая площадка · вместимость, м³':'Кандидат (не утверждён) · вместимость, м³'));
    const controls=el('div',null,'entry-actions'),input=document.createElement('input');input.type='number';input.min=kind==='demand'?'1':'0';input.step='1';input.value=kind==='demand'?point.volumeM3:point.capacityM3;
    input.setAttribute('aria-label',(kind==='demand'?'Объём снега, м³: ':'Вместимость, м³: ')+point.name);
    input.addEventListener('change',()=>{const n=Number(input.value);if(Number.isSafeInteger(n)&&n>=(kind==='demand'?1:0)){point[kind==='demand'?'volumeM3':'capacityM3']=n;changed();}else input.value=kind==='demand'?point.volumeM3:point.capacityM3;});
    const loc=el('button','На карте');loc.type='button';loc.setAttribute('aria-label','Показать на карте: '+point.name);loc.addEventListener('click',()=>{closeDrawer();const m=markers.get(point.id);if(m){map.setView(m.getLatLng(),Math.max(map.getZoom(),14));m.openPopup();}});
    const del=el('button','×');del.type='button';del.title='Удалить точку';del.setAttribute('aria-label','Удалить: '+point.name);del.addEventListener('click',()=>{const j=array.findIndex(v=>v.id===point.id);if(j>=0)array.splice(j,1);changed();});
    controls.append(input,loc,del);entry.append(title,controls);div.append(entry);}
}
// ---------- drawer / dialogs / pick mode ----------
function openDrawer(tab){$('drawer').hidden=false;$('edit-btn').setAttribute('aria-expanded','true');if(tab)showTab(tab);}
function closeDrawer(){if($('drawer').hidden)return;$('drawer').hidden=true;$('edit-btn').setAttribute('aria-expanded','false');}
function showTab(name){for(const t of ['points','truck','file']){const on=t===name;$('tab-'+t).setAttribute('aria-selected',String(on));$('panel-'+t).hidden=!on;}}
function beginAdd(mode){selectedMode=mode;closeDrawer();map.getContainer().style.cursor='crosshair';const b=$('pick-banner');b.hidden=false;b.textContent='Нажмите на дорогу на карте, чтобы добавить '+(mode==='site'?'кандидата':'источник снега')+' (Esc — отмена)';}
function endPick(){selectedMode=null;map.getContainer().style.cursor='';$('pick-banner').hidden=true;}
// ---------- import / export ----------
function saveScenario(){updateTruck();const out={...scenario,meta:{exportedAt:new Date().toISOString(),networkVersion:network.version,app:'snow-site-0.2',note:'Сценарные данные; юридическая допустимость не проверена'}};
  const blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='snowsite-scenario.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
async function loadScenario(file){try{if(file.size>1024*1024)throw Error('Файл слишком большой');let raw;try{raw=JSON.parse(await file.text());}catch{throw Error('файл не является корректным JSON')}
  const ver=raw?.meta?.networkVersion;if(ver&&ver!==network.version)throw Error('сценарий создан для другой версии дорожной сети ('+ver+'), а в приложении '+network.version+'. Импорт отклонён, чтобы не считать на другом графе');
  scenario=sanitizeScenario(raw);truckControls();hasFit=false;changed();fitMap();msg('pick-help','Сценарий загружен.');msg('stats-status','Сценарий загружен — нажмите «Пересчитать»');}
  catch(e){msg('stats-status','Ошибка импорта — сценарий не изменён');msg('pick-help','Импорт отклонён: '+e.message+'. Текущий сценарий сохранён.');openDrawer('file');$('panel-file').prepend(Object.assign(el('p','Импорт отклонён: '+e.message+'. Текущий сценарий сохранён.','field-err'),{role:'alert',id:'import-err'}));}}
async function boot(){try{
  [network,scenario]=await Promise.all([getJson('/api/network'),getJson('/api/demo')]);
  if(window.innerWidth<1000)document.querySelector('.legend').open=false;
  edgeLookup=new Map(network.edges.map(e=>[String(e.id),e]));
  initMap();truckControls();renderEntries();renderPoints();renderResult();fitMap();
  for(const [f,id] of TRUCK_INPUTS)$(id).addEventListener('input',()=>{if(onTruckInput(f,id))changed();});
  $('compare').addEventListener('click',compare);
  let resetTimer=null;$('reset').addEventListener('click',async()=>{const b=$('reset');
    if(b.dataset.armed!=='1'){b.dataset.armed='1';b.textContent='Точно сбросить? Нажмите ещё раз';clearTimeout(resetTimer);resetTimer=setTimeout(()=>{b.dataset.armed='';b.textContent='Сброс';},4000);return;}
    clearTimeout(resetTimer);b.dataset.armed='';b.textContent='Сброс';
    scenario=await getJson('/api/demo');truckControls();hasFit=false;selectedKey='base';changed();fitMap();msg('stats-status','Демо-сценарий восстановлен — нажмите «Пересчитать»');});
  $('new-demand').addEventListener('click',()=>beginAdd('demand'));$('new-site').addEventListener('click',()=>beginAdd('site'));
  $('fit-map').addEventListener('click',fitMap);
  $('export').addEventListener('click',saveScenario);
  $('import').addEventListener('change',e=>{$('import-err')?.remove();if(e.target.files[0])loadScenario(e.target.files[0]);e.target.value='';});
  $('edit-btn').addEventListener('click',()=>$('drawer').hidden?openDrawer():closeDrawer());$('drawer-close').addEventListener('click',()=>{closeDrawer();$('edit-btn').focus();});
  for(const t of ['points','truck','file'])$('tab-'+t).addEventListener('click',()=>showTab(t));
  $('how-btn').addEventListener('click',()=>$('how').showModal());$('how-close').addEventListener('click',()=>$('how').close());
  $('variants').addEventListener('keydown',e=>{if(e.key!=='ArrowRight'&&e.key!=='ArrowLeft')return;const bs=[...$('variants').querySelectorAll('button')],i=bs.indexOf(document.activeElement);if(i<0)return;bs[(i+(e.key==='ArrowRight'?1:bs.length-1))%bs.length].focus();e.preventDefault();});
  document.addEventListener('keydown',e=>{if(e.key!=='Escape')return;if(selectedMode)endPick();else if(!$('drawer').hidden){closeDrawer();$('edit-btn').focus();}});
  await compare();
}catch(e){msg('stats-status','Ошибка: '+e.message);console.error(e);}}
boot();
