import type { GeoJSONSource, Map as MLMap } from "maplibre-gl";
import type { EdgeIndex } from "../api/data";
import type { LonLat, Plan, RouteStep, TaskStatus, Vehicle } from "../api/types";
import { DANGER, DEADHEAD_COLOR, TRUCK_COLOR, isTruckId, vehicleColor } from "../lib/vehicle";

export const SRC = {
  req: "ov-req", done: "ov-done", routes: "ov-routes", pnew: "ov-pnew", phl: "ov-phl", prem: "ov-prem",
} as const;

type FC = GeoJSON.FeatureCollection;
const EMPTY: FC = { type: "FeatureCollection", features: [] };

export function overlaySources(): Record<string, { type: "geojson"; data: FC }> {
  return Object.fromEntries(Object.values(SRC).map((id) => [id, { type: "geojson" as const, data: EMPTY }]));
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type L = any;
const z = (...stops: number[]): L => ["interpolate", ["linear"], ["zoom"], ...stops];
const round = { "line-cap": "round", "line-join": "round" };
const isService: L = ["==", ["get", "kind"], "service"];
const isDead: L = ["!=", ["get", "kind"], "service"];

function routeLayers(prefix: string, source: string, casing = false): L[] {
  const thin: L = ["!=", ["get", "sel"], true];
  const bold: L = ["==", ["get", "sel"], true];
  const mk = (suf: string, flt: L, w: L, dw: L, withCasing: boolean): L[] => [
    ...(withCasing ? [{ id: `${prefix}-casing${suf}`, type: "line", source, filter: ["all", flt, isService], layout: round,
      paint: { "line-color": "#FFFFFF", "line-width": z(12, 4, 16, 10, 18, 16), "line-opacity": ["get", "op"] } }] : []),
    { id: `${prefix}-service${suf}`, type: "line", source, filter: ["all", flt, isService], layout: round,
      paint: { "line-color": ["get", "color"], "line-width": w, "line-opacity": ["get", "op"] } },
    { id: `${prefix}-deadhead${suf}`, type: "line", source, filter: ["all", flt, isDead], layout: { "line-join": "round" },
      paint: { "line-color": ["get", "dh"], "line-width": dw, "line-dasharray": [1.4, 1.1], "line-opacity": ["get", "op"] } },
  ];
  return [
    ...mk("", thin, z(12, 1.5, 16, 3, 18, 4), z(12, 1.2, 16, 2.5, 18, 3.5), casing),
    ...mk("-sel", bold, z(12, 3, 16, 7, 18, 11), z(12, 2.5, 16, 5, 18, 8), true),
  ];
}

export function overlayLayers(): L[] {
  return [
    { id: "req-line", type: "line", source: SRC.req, layout: round,
      paint: { "line-color": "#8FA0B8", "line-width": z(12, 1, 16, 2, 18, 3.5), "line-opacity": 0.8 } },
    { id: "done-line", type: "line", source: SRC.done, layout: round,
      paint: { "line-color": "#9BD3AE", "line-width": z(12, 1.5, 16, 3, 18, 5), "line-opacity": 0.9 } },
    ...routeLayers("routes", SRC.routes),
    { id: "prem-casing", type: "line", source: SRC.prem, layout: round,
      paint: { "line-color": "#FFFFFF", "line-width": z(12, 4, 16, 10, 18, 16) } },
    { id: "prem-line", type: "line", source: SRC.prem, layout: { "line-join": "round" },
      paint: { "line-color": DANGER, "line-width": z(12, 2.5, 16, 6, 18, 10), "line-dasharray": [1.2, 1.0] } },
    { id: "phl-halo", type: "line", source: SRC.phl, layout: round,
      paint: { "line-color": ["get", "color"], "line-width": z(12, 8, 16, 18, 18, 26), "line-opacity": 0.28 } },
    ...routeLayers("pnew", SRC.pnew, true),
  ];
}

export function setSource(map: MLMap, id: string, fc: FC) {
  try {
    const s = map.getSource(id) as GeoJSONSource | undefined;
    s?.setData(fc);
  } catch { /* map already removed */ }
}

// ------------------------------------------------------------------ geometry builders
interface Line { kind: "service" | "deadhead"; done: boolean; coords: LonLat[] }

/** Merge consecutive steps of the same kind/done state into polylines. */
export function buildLines(steps: RouteStep[], edges: EdgeIndex): Line[] {
  const out: Line[] = [];
  for (const s of steps) {
    if (!s.edge_id) continue;
    const g = edges.byId.get(s.edge_id)?.geom;
    if (!g || g.length < 2) continue;
    const kind = s.kind === "service" ? "service" : "deadhead";
    const last = out[out.length - 1];
    const lc = last?.coords[last.coords.length - 1];
    // merge only when the new edge really starts where the previous one ended (no straight jumps over skipped steps)
    const contiguous = !!lc && Math.abs(lc[0] - g[0][0]) < 1e-7 && Math.abs(lc[1] - g[0][1]) < 1e-7;
    if (last && contiguous && last.kind === kind && last.done === s.done) last.coords.push(...g.slice(1));
    else out.push({ kind, done: s.done, coords: g.slice() });
  }
  return out;
}

function lineFeature(l: Line, props: Record<string, unknown>): GeoJSON.Feature {
  return { type: "Feature", properties: { kind: l.kind, done: l.done, ...props }, geometry: { type: "LineString", coordinates: l.coords } };
}

const DEADHEAD_LIMIT_M = 2000;

export function routesFC(vehicles: Vehicle[], edges: EdgeIndex, faded: boolean, selected?: string | null): FC {
  const features: GeoJSON.Feature[] = [];
  for (const v of vehicles) {
    const color = vehicleColor(v.id);
    const dh = isTruckId(v.id) ? TRUCK_COLOR : DEADHEAD_COLOR;
    const sel = v.id === selected;
    const dim = selected && !sel;
    let steps = v.route;
    if (!sel) {
      // remaining part only; deadhead shown for the next ~2 km only
      let dhm = 0;
      steps = v.route.filter((s) => {
        if (s.done) return false;
        if (s.kind === "service") return true;
        dhm += s.length_m;
        return dhm <= DEADHEAD_LIMIT_M;
      });
    }
    for (const l of buildLines(steps, edges)) {
      const op = l.done ? 0.3 : v.status === "broken" ? 0.25 : sel ? 1 : dim ? 0.2 : faded ? 0.3 : 0.55;
      features.push(lineFeature(l, { color, dh, op, vid: v.id, sel }));
    }
  }
  return { type: "FeatureCollection", features };
}

export function planFC(plan: Plan, edges: EdgeIndex): FC {
  const features: GeoJSON.Feature[] = [];
  for (const [vid, steps] of Object.entries(plan.routes)) {
    const color = vehicleColor(vid);
    for (const l of buildLines(steps.filter((s) => !s.done), edges)) features.push(lineFeature(l, { color, dh: DEADHEAD_COLOR, op: 1, vid, sel: false }));
  }
  return { type: "FeatureCollection", features };
}

export function taskEdgesFC(taskIds: string[], edges: EdgeIndex, color: (tid: string) => string): FC {
  const features: GeoJSON.Feature[] = [];
  for (const tid of taskIds) {
    const eid = edges.byTask.get(tid)?.[0];
    const g = eid ? edges.byId.get(eid)?.geom : undefined;
    if (!g || g.length < 2) continue;
    features.push({ type: "Feature", properties: { color: color(tid), kind: "service", done: false, op: 1, sel: false },
      geometry: { type: "LineString", coordinates: g } });
  }
  return { type: "FeatureCollection", features };
}

export function doneFC(statuses: Record<string, TaskStatus>, edges: EdgeIndex): FC {
  const ids = Object.keys(statuses).filter((t) => statuses[t] === "done");
  return taskEdgesFC(ids, edges, () => "#9BD3AE");
}

/** Required streets not yet served: neutral thin gray-blue. */
export function remainingFC(statuses: Record<string, TaskStatus> | undefined, edges: EdgeIndex): FC {
  const ids: string[] = [];
  for (const tid of edges.byTask.keys()) {
    const st = statuses?.[tid];
    if (st !== "done" && st !== "cancelled") ids.push(tid);
  }
  return taskEdgesFC(ids, edges, () => "#8FA0B8");
}
