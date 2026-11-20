import { Marker, type Map as MLMap } from "maplibre-gl";
import type { Vehicle } from "../api/types";
import { isTruckId, vehicleColor } from "../lib/vehicle";
import type { EdgeIndex } from "../api/data";

type LL = [number, number];
const segLen = (a: LL, b: LL) => Math.hypot((b[0] - a[0]) * Math.cos(((a[1] + b[1]) / 2) * Math.PI / 180), b[1] - a[1]);
const polyLen = (g: LL[]) => { let t = 0; for (let i = 0; i + 1 < g.length; i++) t += segLen(g[i], g[i + 1]); return t; };

interface Item {
  marker: Marker; el: HTMLDivElement; inner: HTMLDivElement;
  cur: LL; pts: LL[]; cum: number[]; d: number; spd: number; last: number;
}
const DUR = 250;          // ms: time to travel the path of one snapshot interval
const SNAP_M = 30;        // never tween farther than this without a real path
const M_PER_DEG = 111320;
const meters = (a: LL, b: LL) => segLen(a, b) * M_PER_DEG;

/** HTML vehicle markers with light interpolation between 500 ms snapshots. */
export class VehicleMarkers {
  private items = new Map<string, Item>();
  private raf = 0;
  private edges: EdgeIndex | null = null;
  constructor(private map: MLMap, private onClick?: (id: string) => void) {}

  /** Remaining (not yet shown) part of the current animation, starting at the marker's current point. */
  private remaining(it: Item): LL[] {
    if (it.pts.length < 2 || it.d >= it.cum[it.cum.length - 1]) return [it.cur];
    let i = 1; while (i < it.cum.length - 1 && it.cum[i] < it.d) i++;
    return [it.cur, ...it.pts.slice(i)];
  }
  private setPath(it: Item, pts: LL[]) {
    const cum = [0]; for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + segLen(pts[i - 1], pts[i]));
    it.pts = pts; it.cum = cum; it.d = 0; it.spd = cum[cum.length - 1] / DUR;
  }

  update(vehicles: Vehicle[], selected?: string | null, edges?: EdgeIndex | null) {
    if (edges !== undefined) this.edges = edges;
    const seen = new Set<string>();
    const now = performance.now();
    for (const v of vehicles) {
      if (!v.position) continue;
      seen.add(v.id);
      const target: LL = [v.position.lon, v.position.lat];
      let it = this.items.get(v.id);
      if (!it) {
        const el = document.createElement("div");
        el.className = "vm";
        const dot = document.createElement("div"); dot.className = "vm-dot";
        const inner = document.createElement("div");
        const label = document.createElement("div"); label.className = "vm-label"; label.textContent = v.id;
        dot.appendChild(inner); el.appendChild(dot); el.appendChild(label);
        el.addEventListener("click", (e) => { e.stopPropagation(); this.onClick?.(v.id); });
        const marker = new Marker({ element: el, anchor: "center" }).setLngLat(target).addTo(this.map);
        it = { marker, el, inner, cur: target, pts: [], cum: [0], d: 0, spd: 0, last: now };
        this.items.set(v.id, it);
      } else {
        const trail = v.trail && v.trail.length >= 2 ? (v.trail as LL[]) : null;
        let rest = this.remaining(it);
        if (trail) {
          if (meters(rest[rest.length - 1], trail[0]) > SNAP_M) rest = [trail[0]];   // teleport/gap: snap to trail start
          else if (rest.length === 1 && meters(rest[0], trail[0]) > 0.5) rest = [rest[0]];
          const path = rest.concat(trail);
          if (meters(path[path.length - 1], target) > SNAP_M) { it.cur = target; this.setPath(it, [target]); }
          else {
            it.cur = path[0];
            this.setPath(it, path);
          }
        } else {
          const end = rest[rest.length - 1];
          if (meters(end, target) <= SNAP_M && (rest.length > 1 || meters(end, target) > 0.5)) {
            this.setPath(it, rest.concat([target]));   // short hop (<=30 m): straight is harmless
          } else { it.cur = target; this.setPath(it, [target]); }
        }
        it.last = now;
      }
      it.inner.className = `vm-in ${isTruckId(v.id) ? "truck" : "plow"}`;
      it.inner.style.background = v.status === "broken" ? "#E5322D" : vehicleColor(v.id);
      it.inner.textContent = v.status === "broken" ? "×" : "";
      it.el.classList.toggle("sel", v.id === selected);
      it.el.style.zIndex = v.id === selected ? "5" : "1";
    }
    for (const [id, it] of this.items) if (!seen.has(id)) { it.marker.remove(); this.items.delete(id); }
    if (!this.raf) this.raf = requestAnimationFrame(this.tick);
  }

  private tick = () => {
    this.raf = 0;
    const now = performance.now();
    let busy = false;
    for (const it of this.items.values()) {
      const dt = now - it.last; it.last = now;
      const total = it.cum[it.cum.length - 1];
      if (it.pts.length >= 2 && total > 0 && it.d < total) {
        it.d = Math.min(total, it.d + it.spd * dt);
        let i = 1; while (i < it.cum.length - 1 && it.cum[i] < it.d) i++;
        const l = it.cum[i] - it.cum[i - 1]; const t = l > 0 ? (it.d - it.cum[i - 1]) / l : 1;
        it.cur = [it.pts[i - 1][0] + (it.pts[i][0] - it.pts[i - 1][0]) * t, it.pts[i - 1][1] + (it.pts[i][1] - it.pts[i - 1][1]) * t];
        if (it.d < total) busy = true;
      } else if (it.pts.length) it.cur = it.pts[it.pts.length - 1];
      it.marker.setLngLat(it.cur);
    }
    if (busy) this.raf = requestAnimationFrame(this.tick);
  };

  destroy() {
    cancelAnimationFrame(this.raf); this.raf = 0;
    for (const it of this.items.values()) it.marker.remove();
    this.items.clear();
  }
}
