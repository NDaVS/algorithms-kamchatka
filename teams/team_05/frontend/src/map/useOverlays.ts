import { useEffect, useRef } from "react";
import type { Map as MLMap } from "maplibre-gl";
import type { EdgeIndex } from "../api/data";
import type { ReplanProposal, TaskStatus, Vehicle } from "../api/types";
import { DANGER } from "../lib/vehicle";
import { vehicleColor } from "../lib/vehicle";
import { doneFC, planFC, remainingFC, routesFC, setSource, SRC, taskEdgesFC } from "./layers";

interface Opts {
  vehicles: Vehicle[];
  edges: EdgeIndex | null;
  proposal: ReplanProposal | null;
  statuses?: Record<string, TaskStatus>;
  planId: string | null;
  showDone?: boolean;
  selected?: string | null;
}

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

/** Pushes routes / done streets / proposal overlay into the map sources; rebuilds only when a signature changes. */
export function useOverlays(map: MLMap | null, ready: boolean, o: Opts) {
  const sigRoutes = useRef("");
  const sigDone = useRef("");
  const sigReq = useRef("");
  const sigProp = useRef("");

  useEffect(() => { sigRoutes.current = ""; sigDone.current = ""; sigReq.current = ""; sigProp.current = ""; }, [map]);

  useEffect(() => {
    if (!map || !ready || !o.edges) return;
    const edges = o.edges;
    const pending = o.proposal;

    const rs = o.vehicles.map((v) => {
      let d = 0;
      for (const s of v.route) if (s.done) d++;
      return `${v.id}:${v.status}:${v.route.length}:${d}`;
    }).join("|") + `#${o.selected ?? ""}#${o.planId}#${pending?.id ?? ""}#${edges.byId.size}`;
    if (rs !== sigRoutes.current) {
      sigRoutes.current = rs;
      setSource(map, SRC.routes, routesFC(o.vehicles, edges, !!pending, o.selected));
    }

    if (o.showDone !== false) {
      let n = 0;
      for (const k in o.statuses ?? {}) if (o.statuses![k] === "done") n++;
      const ds = `${n}#${edges.byId.size}`;
      if (ds !== sigDone.current) {
        sigDone.current = ds;
        setSource(map, SRC.done, o.statuses ? doneFC(o.statuses, edges) : EMPTY);
      }
    }
    {
      let nd = 0;
      for (const k in o.statuses ?? {}) if (o.statuses![k] === "done" || o.statuses![k] === "cancelled") nd++;
      const qs = `${nd}#${edges.byId.size}#${edges.byTask.size}`;
      if (qs !== sigReq.current) {
        sigReq.current = qs;
        setSource(map, SRC.req, remainingFC(o.statuses, edges));
      }
    }

    const ps = `${pending?.id ?? ""}#${edges.byId.size}`;
    if (ps !== sigProp.current) {
      sigProp.current = ps;
      if (!pending) {
        setSource(map, SRC.pnew, EMPTY); setSource(map, SRC.phl, EMPTY); setSource(map, SRC.prem, EMPTY);
      } else {
        setSource(map, SRC.pnew, planFC(pending.new_plan, edges));
        const added: string[] = [], owner = new Map<string, string>(), removed: string[] = [];
        const brokenIds = new Set(o.vehicles.filter((v) => v.status === "broken").map((v) => v.id));
        for (const d of pending.diffs) {
          for (const t of d.added_task_ids) { added.push(t); owner.set(t, d.vehicle_id); }
          // red dashes only for tasks freed by a breakdown
          if (brokenIds.has(d.vehicle_id)) removed.push(...d.removed_task_ids);
        }
        setSource(map, SRC.phl, taskEdgesFC(added, edges, (t) => vehicleColor(owner.get(t) ?? "")));
        setSource(map, SRC.prem, taskEdgesFC(removed, edges, () => DANGER));
      }
    }
  });
}
