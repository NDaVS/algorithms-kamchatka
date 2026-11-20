import type { EdgeIndex } from "../api/data";
import type { RouteStep, Task, Vehicle } from "../api/types";
import { fmtKm } from "./format";
import type { Profile } from "../components/ElevationProfile";

export interface DriverInfo {
  step: RouteStep | null;
  remainingKm: number;
  street: string | null;
  nextStreet: string | null;
  warn: { pct: number; dist: number; down: boolean; now: boolean } | null;
  turn: number;
  profile: Profile | null;
  next: { text: string; deadhead: boolean } | null;
  finishT: number | null;
}

export function analyze(v: Vehicle, edges: EdgeIndex | null, task: Task | null, nextTasks: Task[]): DriverInfo {
  const rem = v.route.filter((s) => !s.done);
  const step = rem[0] ?? null;
  const nameOf = (s: RouteStep) => (s.edge_id ? edges?.byId.get(s.edge_id)?.name ?? null : null);
  const slopeOf = (s: RouteStep) => (s.edge_id ? edges?.byId.get(s.edge_id)?.slope ?? 0 : 0);
  let off = 0;
  if (step && v.position?.edge_id && v.position.edge_id === step.edge_id) off = Math.min(v.position.offset_m, step.length_m);

  let curM = -off;
  for (const s of rem) {
    if (step && s.kind === step.kind && (s.kind !== "service" || s.task_id === step.task_id)) curM += s.length_m; else break;
  }
  const remainingKm = Math.max(0, curM) / 1000;
  const street = (step ? nameOf(step) : null) ?? task?.street_name ?? null;
  let nextStreet: string | null = null;
  for (const s of rem.slice(1)) { const n = nameOf(s); if (n && n !== street) { nextStreet = n; break; } }
  nextStreet = nextStreet ?? nextTasks[0]?.street_name ?? null;

  let warn: DriverInfo["warn"] = null;
  let d = -off;
  for (const s of rem) {
    if (d > 500) break;
    const sl = slopeOf(s);
    if (Math.abs(sl) > 4) {
      const now = d <= 0;
      warn = { pct: Math.round(Math.abs(sl)), dist: Math.max(0, Math.round(d / 10) * 10), down: sl < 0, now };
      break;
    }
    d += s.length_m;
  }

  // turn direction at the end of the current street (deg, 0 = straight ahead)
  let turn = 0;
  const geomOf = (s?: RouteStep) => (s?.edge_id ? edges?.byId.get(s.edge_id)?.geom : undefined);
  const g0 = geomOf(step ?? undefined), g1 = geomOf(rem[1]);
  if (g0 && g0.length > 1 && g1 && g1.length > 1) {
    const brg = (a: number[], b: number[]) => Math.atan2((b[0] - a[0]) * Math.cos(a[1] * Math.PI / 180), b[1] - a[1]) * 180 / Math.PI;
    turn = brg(g1[0], g1[1]) - brg(g0[g0.length - 2], g0[g0.length - 1]);
    turn = ((turn + 540) % 360) - 180;
  }

  let profile: Profile | null = null;
  if (rem.length && edges) {
    const pts: [number, number][] = [[0, 0]], slopes: number[] = [];
    let dist = 0, el = 0;
    for (let i = 0; i < rem.length && (dist < 1500 || i === 0); i++) {
      const len = rem[i].length_m - (i === 0 ? off : 0);
      if (len <= 0) continue;
      const sl = slopeOf(rem[i]);
      el += (sl / 100) * len; dist += len;
      pts.push([dist, el]); slopes.push(sl);
    }
    if (pts.length > 1) profile = { pts, slopes, len: dist };
  }

  let next: DriverInfo["next"] = null;
  if (step) {
    const j = rem.findIndex((s, i) => i > 0 && (s.kind !== step.kind || (s.kind === "service" && s.task_id !== step.task_id)));
    if (j < 0) next = { text: step.kind === "return" ? "Далее: стоянка на базе" : "Далее: возвращение на базу", deadhead: step.kind !== "return" };
    else if (rem[j].kind === "service") next = { text: `Далее: чистка · ${nameOf(rem[j]) ?? "следующий участок"}`, deadhead: false };
    else {
      let km = 0;
      for (let i = j; i < rem.length && rem[i].kind !== "service"; i++) km += rem[i].length_m;
      next = { text: `Далее: перегон ${fmtKm(km / 1000)}`, deadhead: true };
    }
  }
  const finishT = v.route.length && v.status !== "broken" ? Math.max(v.route[v.route.length - 1].t_end, 0) : null;
  return { step, remainingKm, street, nextStreet, warn, turn, profile, next, finishT };
}
