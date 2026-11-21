export class DomainError extends Error {
  constructor(message, path='', code='invalid_input', status=422){super(message);this.name='DomainError';this.path=path;this.code=code;this.status=status;}
}
export const requireValid=(condition,message,path,code,status)=>{if(!condition)throw new DomainError(message,path,code,status);};
export const int=(n,min=0,max=1_000_000)=>Number.isSafeInteger(n)&&n>=min&&n<=max;
export const id=x=>typeof x==='string'&&x.trim().length>0&&x.length<=200;
export function validateNetwork(network,closed=[]){
  requireValid(network&&network.nodes&&typeof network.nodes==='object'&&!Array.isArray(network.nodes)&&Array.isArray(network.edges),'Некорректная сеть','network');
  const nodes=Object.entries(network.nodes);requireValid(nodes.length>0&&nodes.length<=100000&&network.edges.length<=250000,'Network size limit','network','resource_limit');
  for(const [k,p] of nodes)requireValid(id(k)&&Array.isArray(p)&&p.length===2&&p.every(Number.isFinite)&&Math.abs(p[0])<=180&&Math.abs(p[1])<=90,'Invalid node','network.nodes');
  const ids=new Set();for(const e of network.edges){requireValid(e&&(id(e.id)||Number.isSafeInteger(e.id))&&!ids.has(String(e.id))&&Object.hasOwn(network.nodes,String(e.from))&&Object.hasOwn(network.nodes,String(e.to))&&Number.isFinite(e.km)&&e.km>0&&e.km<=10000&&typeof e.oneway==='boolean'&&(e.routable===undefined||typeof e.routable==='boolean'),'Invalid edge','network.edges');ids.add(String(e.id));}
  requireValid(Array.isArray(closed)&&closed.every(x=>typeof x==='string'&&ids.has(x))&&new Set(closed).size===closed.length,'Unknown or duplicate closed edge','closedEdgeIds');
}
export function truckCapacity(t){
  requireValid(t&&int(t.bodyM3,1)&&Number.isFinite(t.driveKmh)&&t.driveKmh>=0.1&&t.driveKmh<=200,'Invalid truck body or speed','truck');
  requireValid((t.payloadKg==null)===(t.snowDensityKgM3==null),'Payload and density must be supplied together','truck');
  if(t.payloadKg!=null)requireValid(int(t.payloadKg,1)&&int(t.snowDensityKgM3,1),'Invalid payload/density','truck');
  requireValid(t.rateRubPerHour==null||(Number.isFinite(t.rateRubPerHour)&&t.rateRubPerHour>=0&&t.rateRubPerHour<=1e7),'Invalid rate','truck.rateRubPerHour');
  const q=t.payloadKg==null?t.bodyM3:Math.min(t.bodyM3,Math.floor(t.payloadKg/t.snowDensityKgM3));requireValid(q>=1,'Truck cannot carry 1 m³','truck');return q;
}
export function validateLegacy(s,network){
  requireValid(s&&Array.isArray(s.demands)&&Array.isArray(s.sites)&&s.demands.length>0&&s.sites.length>0&&s.demands.length<=50&&s.sites.length<=30,'Invalid scenario size','scenario');
  validateNetwork(network,s.closedEdgeIds??[]);
  const ids=new Set();for(const group of ['demands','sites'])for(const x of s[group]){
    requireValid(x&&id(x.id)&&!ids.has(x.id)&&Object.hasOwn(network.nodes,String(x.nodeId)),'Invalid ID/node','scenario.'+group);ids.add(x.id);
    requireValid(group==='demands'?int(x.volumeM3,1):int(x.capacityM3)&&['existing','candidate'].includes(x.kind),'Invalid volume, capacity or kind','scenario.'+group);
  }
  if(s.networkVersion!=null)requireValid(s.networkVersion===network.version,'Network version mismatch','networkVersion','network_mismatch',409);
  const q=truckCapacity({...s.truck,snowDensityKgM3:s.snowDensityKgM3});
  // A conservative bound including fragmentation by site. Legacy uses per-trip objects and quadratic swaps.
  const upper=s.demands.reduce((v,d)=>v+Math.min(d.volumeM3,Math.ceil(d.volumeM3/q)+s.sites.length),0);
  requireValid(upper<=500&&upper*upper*Math.max(1,s.sites.length)<=2_000_000,'Legacy trip work limit; use v1','scenario','resource_limit');
}
