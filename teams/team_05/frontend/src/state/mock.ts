// Mock mode (?mock=1): a tiny in-browser fake backend so the UI works without the server.
import type {
  Basemap, DriverView, LonLat, Metrics, Plan, Position, ReplanProposal, RouteStep, ServerMessage, SimEvent,
  SimState, StepKind, Task, TaskStatus, Vehicle, VehicleDiff, NewRequestBody,
} from "../api/types";

export const isMock = typeof location !== "undefined" && new URLSearchParams(location.search).get("mock") === "1";

const COLS = 9, ROWS = 8, LON0 = 158.61, LAT0 = 53.01, DX = 0.0075, DY = 0.0045;
const nid = (r: number, c: number) => r * COLS + c;
const nlon = (n: number) => LON0 + (n % COLS) * DX;
const nlat = (n: number) => LAT0 + Math.floor(n / COLS) * DY;
const ROW_NAMES = ["проспект Карла Маркса", "улица Ленинская", "улица Советская", "проспект Победы", "улица Океанская",
  "улица Пограничная", "улица Молчанова", "улица Тушканова"];
const COL_NAMES = ["улица Вилюйская", "улица Мишенная", "улица Кроноцкая", "улица Чубарова", "улица Автомобилистов",
  "улица Зеркальная", "улица Горького", "улица Заводская", "улица Бохняка"];
const elev = (n: number) => 45 * Math.exp(-(((n % COLS) - 4) ** 2 + (Math.floor(n / COLS) - 3) ** 2) / 5);

function hav(a: LonLat, b: LonLat): number {
  const R = 6371000, rad = Math.PI / 180;
  const dLat = (b[1] - a[1]) * rad, dLon = (b[0] - a[0]) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

interface MEdge { id: string; u: number; v: number; len: number; slope: number; name: string; seg: string; geom: LonLat[]; major: boolean }
interface Seg { key: string; a: number; b: number; name: string; major: boolean; len: number }
const edges = new Map<string, MEdge>();
const segs: Seg[] = [];

function buildGrid() {
  const add = (a: number, b: number, name: string, major: boolean) => {
    const pa: LonLat = [nlon(a), nlat(a)], pb: LonLat = [nlon(b), nlat(b)];
    const len = hav(pa, pb);
    const key = `${a}~${b}`;
    segs.push({ key, a, b, name, major, len });
    edges.set(`${a}-${b}-0`, { id: `${a}-${b}-0`, u: a, v: b, len, slope: ((elev(b) - elev(a)) / len) * 100, name, seg: key, geom: [pa, pb], major });
    edges.set(`${b}-${a}-0`, { id: `${b}-${a}-0`, u: b, v: a, len, slope: ((elev(a) - elev(b)) / len) * 100, name, seg: key, geom: [pb, pa], major });
  };
  for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS - 1; c++) add(nid(r, c), nid(r, c + 1), ROW_NAMES[r], r === 3);
  for (let c = 0; c < COLS; c++) for (let r = 0; r < ROWS - 1; r++) add(nid(r, c), nid(r + 1, c), COL_NAMES[c], c === 4);
}
buildGrid();

export function mockBasemap(): Basemap {
  const fc = (features: GeoJSON.Feature[]): GeoJSON.FeatureCollection => ({ type: "FeatureCollection", features });
  const rect = (x0: number, y0: number, x1: number, y1: number, props: GeoJSON.GeoJsonProperties = {}): GeoJSON.Feature => ({
    type: "Feature", properties: props,
    geometry: { type: "Polygon", coordinates: [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]] },
  });
  const roads = segs.map((s): GeoJSON.Feature => ({
    type: "Feature",
    properties: { layer: "roads", highway: s.major ? "primary" : "residential", name: s.name },
    geometry: { type: "LineString", coordinates: [[nlon(s.a), nlat(s.a)], [nlon(s.b), nlat(s.b)]] },
  }));
  const buildings: GeoJSON.Feature[] = [];
  for (let r = 0; r < ROWS - 1; r++) for (let c = 0; c < COLS - 1; c++) {
    if ((r * 7 + c * 3) % 4 === 0) continue;
    const x = LON0 + c * DX, y = LAT0 + r * DY;
    buildings.push(rect(x + DX * 0.25, y + DY * 0.25, x + DX * 0.45, y + DY * 0.45, { layer: "buildings" }));
    buildings.push(rect(x + DX * 0.55, y + DY * 0.55, x + DX * 0.78, y + DY * 0.75, { layer: "buildings" }));
  }
  return {
    bbox: [158.6, 53.0, 158.68, 53.07],
    layers: {
      roads: fc(roads), buildings: fc(buildings),
      water: fc([rect(158.6, 53.044, 158.68, 53.07, { layer: "water" })]),
      parks: fc([
        rect(LON0 + 2 * DX + 0.0006, LAT0 + 4 * DY + 0.0004, LON0 + 3 * DX - 0.0006, LAT0 + 5 * DY - 0.0004, { layer: "parks" }),
        rect(LON0 + 6 * DX + 0.0006, LAT0 + 0.0004, LON0 + 7 * DX - 0.0006, LAT0 + DY - 0.0004, { layer: "parks" }),
      ]),
    },
  };
}

// ------------------------------------------------------------------ mock simulation state
interface MV {
  id: string; kind: "plow" | "truck"; name: string; steps: RouteStep[]; idx: number; off: number;
  broken: boolean; dh: number; sv: number; fuel: number; started: boolean;
}
interface Pending { proposal: ReplanProposal; routes: Map<string, RouteStep[]>; reassign: Map<string, string[]> }

let emit: (m: ServerMessage) => void = () => {};
let m: {
  t: number; running: boolean; speed: number; vehicles: MV[]; tasks: Map<string, Task>; segTask: Map<string, string>;
  events: SimEvent[]; evN: number; propN: number; planN: number; planId: string; pending: Pending | null; reqN: number;
};

const seg2task = () => m.segTask;
const speedOf = (v: MV, s: RouteStep) => (s.kind === "service" ? 15 : v.kind === "truck" ? 30 : 35) / 3.6;

function mkStep(kind: StepKind, a: number, b: number, loaded = false): RouteStep {
  const e = edges.get(`${a}-${b}-0`)!;
  const tid = kind === "service" ? m.segTask.get(e.seg) ?? null : null;
  return {
    kind, edge_id: e.id, task_id: tid, from_node: a, to_node: b, length_m: e.len, loaded,
    t_start: 0, t_end: 0, fuel_l: (e.len / 1000) * (kind === "service" ? 0.6 : 0.38), done: false,
  };
}
function gridPath(a: number, b: number): number[] {
  const out = [a];
  let r = Math.floor(a / COLS), c = a % COLS;
  const rb = Math.floor(b / COLS), cb = b % COLS;
  while (c !== cb) { c += c < cb ? 1 : -1; out.push(nid(r, c)); }
  while (r !== rb) { r += r < rb ? 1 : -1; out.push(nid(r, c)); }
  return out;
}
function pathSteps(a: number, b: number, kind: StepKind, loaded = false): RouteStep[] {
  const p = gridPath(a, b), out: RouteStep[] = [];
  for (let i = 0; i + 1 < p.length; i++) out.push(mkStep(kind, p[i], p[i + 1], loaded));
  return out;
}
function newTask(segKey: string, source: "initial" | "request", vid: string | null): Task {
  const s = segs.find((x) => x.key === segKey)!;
  const id = `T-${String(source === "initial" ? m.tasks.size + 1 : 270 + ++m.reqN).padStart(4, "0")}`;
  const t: Task = {
    id, edge_ids: [`${s.a}-${s.b}-0`, `${s.b}-${s.a}-0`], u: s.a, v: s.b, street_name: s.name, length_m: s.len, passes: 1,
    passes_done: 0, status: "assigned", assigned_vehicle: vid, priority: source === "request" ? 3 : 2, source,
  };
  m.tasks.set(id, t); m.segTask.set(segKey, id);
  return t;
}

function init() {
  m = {
    t: 0, running: false, speed: 1, vehicles: [], tasks: new Map(), segTask: new Map(), events: [], evN: 0, propN: 0,
    planN: 1, planId: "P-0001", pending: null, reqN: 0,
  };
  const segOf = (a: number, b: number) => edges.get(`${a}-${b}-0`)!.seg;
  for (let k = 0; k < 4; k++) {
    const id = `П-0${k + 1}`, r1 = 2 * k, r2 = 2 * k + 1;
    const tk = (a: number, b: number) => { const s = segOf(a, b); if (!m.segTask.has(s)) newTask(s, "initial", id); };
    const steps: RouteStep[] = [];
    for (let c = 0; c < COLS - 1; c++) tk(nid(r1, c), nid(r1, c + 1));
    tk(nid(r1, COLS - 1), nid(r2, COLS - 1));
    for (let c = COLS - 1; c > 0; c--) tk(nid(r2, c), nid(r2, c - 1));
    steps.push(...pathSteps(0, nid(r1, 0), "deadhead"));
    for (let c = 0; c < COLS - 1; c++) steps.push(mkStep("service", nid(r1, c), nid(r1, c + 1)));
    steps.push(mkStep("service", nid(r1, COLS - 1), nid(r2, COLS - 1)));
    for (let c = COLS - 1; c > 0; c--) steps.push(mkStep("service", nid(r2, c), nid(r2, c - 1)));
    steps.push(...pathSteps(nid(r2, 0), 0, "return"));
    m.vehicles.push({ id, kind: "plow", name: `Снегоуборщик ${id}`, steps, idx: 0, off: 0, broken: false, dh: 0, sv: 0, fuel: 0, started: false });
  }
  const ts: RouteStep[] = [
    ...pathSteps(0, nid(ROWS - 1, 0), "deadhead"), ...pathSteps(nid(ROWS - 1, 0), nid(ROWS - 1, COLS - 1), "deadhead", true),
    ...pathSteps(nid(ROWS - 1, COLS - 1), 0, "return"),
  ];
  m.vehicles.push({ id: "С-01", kind: "truck", name: "Самосвал С-01", steps: ts, idx: 0, off: 0, broken: false, dh: 0, sv: 0, fuel: 0, started: false });
}
init();

export function mockEdges(): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: Array.from(edges.values()).map((e): GeoJSON.Feature => {
      const tid = m.segTask.get(e.seg) ?? null;
      return {
        type: "Feature",
        properties: {
          id: e.id, name: e.name, required: tid !== null, task_id: tid, status: tid ? m.tasks.get(tid)?.status ?? null : null,
          slope_pct: e.slope, length_m: e.len,
        },
        geometry: { type: "LineString", coordinates: e.geom },
      };
    }),
  };
}

// ---- helpers
function cur(v: MV): RouteStep | null { return v.idx < v.steps.length ? v.steps[v.idx] : null; }
function retime(v: MV) {
  let tt = m.t;
  for (let i = v.idx; i < v.steps.length; i++) {
    const s = v.steps[i], sp = speedOf(v, s);
    const len = i === v.idx ? s.length_m - v.off : s.length_m;
    s.t_start = tt; tt += len / sp; s.t_end = tt;
  }
}
function finishT(v: MV): number | null {
  if (!v.steps.length || v.broken) return null;
  retime(v);
  return v.steps.length > v.idx ? v.steps[v.steps.length - 1].t_end : null;
}
function statusOf(v: MV): Vehicle["status"] {
  if (v.broken) return "broken";
  const s = cur(v);
  if (!s) return "at_depot";
  if (!v.started && m.t === 0) return "idle";
  if (s.kind === "service") return "servicing";
  if (s.kind === "return") return "returning";
  return s.loaded ? "loaded" : "deadheading";
}
function position(v: MV): Position {
  const s = cur(v) ?? v.steps[v.steps.length - 1];
  const e = edges.get(s.edge_id!)!;
  const f = cur(v) ? v.off / e.len : 1;
  const [a, b] = e.geom;
  const heading = (Math.atan2((b[0] - a[0]) * Math.cos((a[1] * Math.PI) / 180), b[1] - a[1]) * 180) / Math.PI;
  return {
    lon: a[0] + (b[0] - a[0]) * f, lat: a[1] + (b[1] - a[1]) * f, edge_id: e.id,
    offset_m: cur(v) ? v.off : e.len, heading_deg: (heading + 360) % 360,
  };
}
function toVehicle(v: MV): Vehicle {
  retime(v);
  const from = Math.max(0, v.idx - 2);
  const s = cur(v);
  return {
    id: v.id, kind: v.kind, name: v.name, depot_node: 0, start_node: 0, service_speed_kmh: 15, deadhead_speed_kmh: 35,
    capacity_m3: v.kind === "truck" ? 12 : 0, status: statusOf(v), loaded: !!s?.loaded, position: position(v),
    route: v.steps.slice(from).map((x) => ({ ...x })), route_index: v.idx - from,
    current_task_id: s?.kind === "service" ? s.task_id : null, distance_km: (v.dh + v.sv) / 1000, fuel_used_l: v.fuel,
  };
}
function remMetrics(routes: Map<string, RouteStep[]>): Metrics {
  let dh = 0, sv = 0, fuel = 0, tmax = 0;
  for (const [vid, steps] of routes) {
    const v = m.vehicles.find((x) => x.id === vid)!;
    let tt = 0;
    for (const s of steps) {
      if (s.kind === "service") sv += s.length_m; else dh += s.length_m;
      fuel += s.fuel_l; tt += s.length_m / speedOf(v, s);
    }
    tmax = Math.max(tmax, tt);
  }
  return { deadhead_km: dh / 1000, service_km: sv / 1000, total_time_min: tmax / 60, fuel_l: fuel, objective: null };
}
function remRoutes(): Map<string, RouteStep[]> {
  const out = new Map<string, RouteStep[]>();
  for (const v of m.vehicles) if (!v.broken) out.set(v.id, v.steps.slice(v.idx));
  return out;
}
function pushEvent(type: SimEvent["type"], text: string, payload: Record<string, unknown> = {}): SimEvent {
  const ev: SimEvent = { id: `E-${String(++m.evN).padStart(4, "0")}`, t: m.t, type, payload, text };
  m.events.push(ev); m.events = m.events.slice(-30);
  emit({ type: "event", data: ev });
  return ev;
}
function streetOf(v: MV): string {
  const s = cur(v) ?? v.steps[v.steps.length - 1];
  return edges.get(s.edge_id!)!.name;
}

function snapshot(): SimState {
  const statuses: Record<string, TaskStatus> = {};
  const sum = { total: 0, pending: 0, assigned: 0, in_progress: 0, done: 0, cancelled: 0, done_km: 0, total_km: 0 };
  const active = new Set<string>();
  for (const v of m.vehicles) { const s = cur(v); if (s?.kind === "service" && s.task_id && !v.broken) active.add(s.task_id); }
  for (const t of m.tasks.values()) {
    const st: TaskStatus = t.status === "done" ? "done" : active.has(t.id) ? "in_progress" : t.status;
    statuses[t.id] = st; sum.total++; sum[st]++; sum.total_km += t.length_m / 1000;
    if (st === "done") sum.done_km += t.length_m / 1000;
  }
  const vehicles = m.vehicles.map(toVehicle);
  const plan = remMetrics(new Map(m.vehicles.map((v) => [v.id, v.steps])));
  const done = m.vehicles.reduce((a, v) => ({ dh: a.dh + v.dh, sv: a.sv + v.sv, f: a.f + v.fuel }), { dh: 0, sv: 0, f: 0 });
  const metrics: Metrics = { deadhead_km: done.dh / 1000, service_km: done.sv / 1000, total_time_min: m.t / 60, fuel_l: done.f, objective: null };
  const base: Metrics = { ...plan, deadhead_km: plan.deadhead_km * 1.45, total_time_min: plan.total_time_min * 1.12, fuel_l: plan.fuel_l * 1.1 };
  const pct = (a: number, b: number) => (b ? ((a - b) / b) * 100 : 0);
  const p = m.pending?.proposal ?? null;
  return {
    t: m.t, clock: "", speed: m.speed, running: m.running, vehicles, task_statuses: statuses, tasks_summary: sum, metrics,
    plan_metrics: plan, baseline_metrics: base,
    comparison: {
      baseline: base, optimized: plan, deadhead_pct: pct(plan.deadhead_km, base.deadhead_km),
      time_pct: pct(plan.total_time_min, base.total_time_min), fuel_pct: pct(plan.fuel_l, base.fuel_l), objective_pct: -21,
    },
    plan_id: m.planId,
    pending_proposal: p ? { ...p, auto_apply_in_s: Math.max(0, (p.auto_apply_at ?? 0) - Date.now() / 1000) } : null,
    recent_events: m.events.slice(-8),
  };
}
const pushSnapshot = () => emit({ type: "snapshot", data: snapshot() });

function advance(v: MV, dt: number) {
  if (v.broken) return;
  let rem = dt;
  while (rem > 0 && v.idx < v.steps.length) {
    const s = v.steps[v.idx], sp = speedOf(v, s), left = s.length_m - v.off, need = left / sp;
    const frac = need <= rem ? left : sp * rem;
    const k = s.length_m ? frac / s.length_m : 0;
    if (s.kind === "service") v.sv += frac; else v.dh += frac;
    v.fuel += s.fuel_l * k;
    if (need <= rem) { rem -= need; finishStep(v, s); v.idx++; v.off = 0; } else { v.off += sp * rem; rem = 0; }
  }
}
function finishStep(v: MV, s: RouteStep) {
  s.done = true;
  if (s.kind !== "service" || !s.task_id) return;
  const open = m.vehicles.some((w) => w.steps.some((x) => x.task_id === s.task_id && !x.done));
  const t = m.tasks.get(s.task_id);
  if (t && !open) {
    t.status = "done"; t.passes_done = t.passes;
    pushEvent("task_done", `${v.id} завершил участок ${t.id}`, { task_id: t.id, vehicle_id: v.id });
  }
}

// ---- proposals
function reroute(helper: MV, added: RouteStep[]): RouteStep[] {
  const head = helper.steps.slice(0, helper.idx + 1);
  const tail = helper.steps.slice(helper.idx + 1).filter((s) => s.kind !== "return");
  let last = (tail[tail.length - 1] ?? head[head.length - 1])?.to_node ?? 0;
  const out = [...head, ...tail].map((s) => ({ ...s }));
  for (const s of added) {
    out.push(...pathSteps(last, s.from_node, "deadhead"), { ...s, done: false });
    last = s.to_node;
  }
  out.push(...pathSteps(last, 0, "return"));
  return out;
}
function makeProposal(ev: SimEvent, helper: MV, added: RouteStep[], broken: MV | null, reasonText: string) {
  if (m.pending) { m.pending.proposal.status = "expired"; }
  const before = remRoutes();
  const after = new Map(before);
  const hr = reroute(helper, added);
  after.set(helper.id, hr.slice(helper.idx));
  const routes = new Map<string, RouteStep[]>([[helper.id, hr]]);
  const tids = Array.from(new Set(added.map((s) => s.task_id!).filter(Boolean)));
  const addedKm = hr.slice(helper.idx).reduce((a, s) => a + s.length_m, 0) / 1000 - before.get(helper.id)!.reduce((a, s) => a + s.length_m, 0) / 1000;
  const oldF = finishT(helper);
  const hv: MV = { ...helper, steps: hr };
  const newF = finishT(hv);
  const diffs: VehicleDiff[] = [{
    vehicle_id: helper.id, added_task_ids: tids, removed_task_ids: [], delta_km: addedKm, old_finish_t: oldF, new_finish_t: newF,
    delta_finish_min: oldF !== null && newF !== null ? (newF - oldF) / 60 : 0,
  }];
  if (broken) {
    diffs.unshift({
      vehicle_id: broken.id, added_task_ids: [], removed_task_ids: tids, delta_km: -tids.reduce((a, t) => a + (m.tasks.get(t)?.length_m ?? 0), 0) / 1000,
      old_finish_t: null, new_finish_t: null, delta_finish_min: 0,
    });
  }
  const plan: Plan = {
    id: `P-${String(m.planN + 1).padStart(4, "0")}`, created_at: m.t, solver: "mock", weights: { deadhead: 0.5, time: 0.3, fuel: 0.2 },
    routes: Object.fromEntries(Array.from(after).map(([k, s]) => [k, s.map((x) => ({ ...x }))])), vehicle_metrics: {},
    metrics: remMetrics(after), unassigned_task_ids: [], plan_start_t: m.t,
  };
  const proposal: ReplanProposal = {
    id: `R-${String(++m.propN).padStart(4, "0")}`, status: "pending", reason_event_id: ev.id, reason_text: reasonText, created_at: m.t,
    diffs, metrics_before: remMetrics(before), metrics_after: plan.metrics, new_plan: plan,
    auto_apply_at: Date.now() / 1000 + 8, auto_apply_in_s: 8,
  };
  m.pending = { proposal, routes, reassign: new Map([[helper.id, tids]]) };
  emit({ type: "proposal", data: proposal });
  pushSnapshot();
}
function pickHelper(excl?: MV): MV {
  const cand = m.vehicles.filter((v) => v.kind === "plow" && !v.broken && v !== excl);
  return cand.sort((a, b) => (a.steps.length - a.idx) - (b.steps.length - b.idx))[0];
}
function applyPending(id?: string) {
  const p = m.pending;
  if (!p || (id && p.proposal.id !== id)) return null;
  for (const [vid, steps] of p.routes) {
    const v = m.vehicles.find((x) => x.id === vid)!;
    v.steps = steps;
  }
  for (const d of p.proposal.diffs) {
    for (const t of d.removed_task_ids) { const bv = m.vehicles.find((x) => x.id === d.vehicle_id)!; bv.steps = bv.steps.slice(0, bv.idx + (bv.broken ? 0 : 1)); void t; }
    for (const t of d.added_task_ids) { const tk = m.tasks.get(t); if (tk) { tk.assigned_vehicle = d.vehicle_id; tk.status = "assigned"; } }
  }
  m.planId = p.proposal.new_plan.id; m.planN++;
  p.proposal.status = "applied"; m.pending = null;
  pushEvent("plan_applied", `Применён новый план (${p.proposal.id})`, { proposal_id: p.proposal.id });
  emit({ type: "proposal", data: null });
  emit({ type: "plan", data: p.proposal.new_plan });
  pushSnapshot();
  return p.proposal.new_plan;
}

export const mockCommands = {
  control(action: "start" | "pause" | "reset", speed?: number | null): SimState {
    if (action === "reset") { const run = false; const sp = m.speed; init(); m.running = run; m.speed = sp; emit({ type: "proposal", data: null }); }
    if (action === "start") m.running = true;
    if (action === "pause") m.running = false;
    if (speed) m.speed = speed;
    if (m.running) m.vehicles.forEach((v) => (v.started = true));
    const s = snapshot(); emit({ type: "snapshot", data: s }); return s;
  },
  breakdown(vid: string): SimEvent {
    const v = m.vehicles.find((x) => x.id === vid);
    if (!v) throw new Error("404 vehicle not found");
    v.broken = true;
    const ev = pushEvent("breakdown", `Поломка: ${v.id} остановился на ${streetOf(v)}`, { vehicle_id: v.id });
    const rest = v.steps.slice(v.idx).filter((s) => s.kind === "service").map((s) => ({ ...s }));
    const helper = pickHelper(v);
    if (helper && rest.length) {
      makeProposal(ev, helper, rest, v, `${v.id} вышел из строя. Его ${rest.length} уч. передаются ${helper.id}, остальные маршруты не меняются.`);
    } else pushSnapshot();
    return ev;
  },
  problem(vid: string, kind: string, comment?: string | null): SimEvent {
    if (kind === "breakdown" || kind === "stuck") return mockCommands.breakdown(vid);
    const v = m.vehicles.find((x) => x.id === vid);
    if (!v) throw new Error("404 vehicle not found");
    const cs = cur(v);
    if (kind === "already_clean" && cs?.task_id) return mockCommands.complete(cs.task_id, vid);
    const ev = pushEvent("info", `${vid}: ${kind === "blocked" ? "проезда нет" : "сообщение водителя"}${comment ? ` (${comment})` : ""}`, { vehicle_id: vid, kind });
    pushSnapshot();
    return ev;
  },
  request(body: NewRequestBody): SimEvent {
    const free = segs.filter((s) => !m.segTask.has(s.key));
    if (!free.length) throw new Error("409 no free streets in mock");
    let seg = free[Math.floor(Math.random() * free.length)];
    if (body.lon != null && body.lat != null) {
      const d = (s: Seg) => Math.hypot((nlon(s.a) + nlon(s.b)) / 2 - body.lon!, (nlat(s.a) + nlat(s.b)) / 2 - body.lat!);
      seg = free.reduce((a, b) => (d(b) < d(a) ? b : a));
    } else if (body.edge_id && edges.get(body.edge_id) && free.some((s) => s.key === edges.get(body.edge_id!)!.seg)) {
      seg = free.find((s) => s.key === edges.get(body.edge_id!)!.seg)!;
    }
    const helper = pickHelper();
    const t = newTask(seg.key, "request", helper.id);
    const ev = pushEvent("new_request", `Новая заявка ${t.id}: расчистить ${seg.name}`, { task_id: t.id });
    makeProposal(ev, helper, [mkStep("service", seg.a, seg.b)], null, `Поступила заявка на ${seg.name}. Участок добавлен в маршрут ${helper.id}.`);
    return ev;
  },
  apply(id: string): Plan { const p = applyPending(id); if (!p) throw new Error("409 not pending"); return p; },
  reject(id: string) {
    if (!m.pending || m.pending.proposal.id !== id) throw new Error("409 not pending");
    m.pending.proposal.status = "rejected"; m.pending = null;
    pushEvent("proposal_rejected", `Предложение ${id} отклонено`);
    emit({ type: "proposal", data: null }); pushSnapshot();
    return { ok: true };
  },
  complete(taskId: string, vid: string): SimEvent {
    const v = m.vehicles.find((x) => x.id === vid);
    if (!v) throw new Error("404");
    const s = cur(v);
    if (s?.task_id === taskId) { finishStep(v, s); v.idx++; v.off = 0; }
    v.steps = v.steps.filter((x, i) => !(x.task_id === taskId && i >= v.idx));
    const t = m.tasks.get(taskId);
    if (t) { t.status = "done"; t.passes_done = t.passes; }
    const ev = pushEvent("task_done", `${vid} завершил участок ${taskId}`, { task_id: taskId, vehicle_id: vid });
    pushSnapshot();
    return ev;
  },
  tasks(): Task[] { snapshot(); return Array.from(m.tasks.values()); },
  driver(vid: string): DriverView {
    const s = snapshot();
    const v = s.vehicles.find((x) => x.id === vid) ?? s.vehicles[0];
    const tasks = Array.from(m.tasks.values());
    const rem = v.route.filter((x) => !x.done && x.kind === "service" && x.task_id);
    const ids = Array.from(new Set(rem.map((x) => x.task_id!)));
    const get = (id: string) => tasks.find((t) => t.id === id) ?? null;
    return {
      vehicle: v, current_task: v.current_task_id ? get(v.current_task_id) : null,
      next_tasks: ids.filter((i) => i !== v.current_task_id).slice(0, 5).map(get).filter((t): t is Task => !!t),
      route_geojson: null, t: s.t, clock: "",
    };
  },
};

export function startMock(onMsg: (msg: ServerMessage) => void): () => void {
  emit = onMsg;
  let n = 0;
  const timer = window.setInterval(() => {
    if (m.running) {
      const dt = 0.1 * m.speed;
      m.t += dt;
      const sub = Math.ceil(dt / 5);
      for (let i = 0; i < sub; i++) m.vehicles.forEach((v) => advance(v, dt / sub));
    }
    if (m.pending && Date.now() / 1000 >= (m.pending.proposal.auto_apply_at ?? Infinity)) applyPending();
    if (++n % 5 === 0) pushSnapshot();
  }, 100);
  pushSnapshot();
  return () => { window.clearInterval(timer); emit = () => {}; };
}
void seg2task;
