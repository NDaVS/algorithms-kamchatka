import type { Basemap, LonLat } from "./types";
import { api } from "./client";
import { isMock, mockBasemap, mockEdges } from "../state/mock";

export interface EdgeInfo {
  id: string; name: string | null; slope: number; length: number;
  taskId: string | null; required: boolean; geom: LonLat[];
}
export interface EdgeIndex { byId: Map<string, EdgeInfo>; byTask: Map<string, string[]> }

const EMPTY_FC: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
export const DEFAULT_BBOX: Basemap["bbox"] = [158.6, 53.0, 158.68, 53.07];

export function emptyBasemap(): Basemap {
  return { bbox: DEFAULT_BBOX, layers: { roads: EMPTY_FC, water: EMPTY_FC, parks: EMPTY_FC, buildings: EMPTY_FC } };
}

/** Accepts the contract shape {bbox, layers} and (defensively) the flat basemap.geojson file shape. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function normalizeBasemap(raw: any): Basemap {
  const out = emptyBasemap();
  if (raw?.bbox) out.bbox = raw.bbox;
  if (raw?.layers) {
    for (const k of ["roads", "water", "parks", "buildings"] as const) if (raw.layers[k]) out.layers[k] = raw.layers[k];
  } else if (Array.isArray(raw?.features)) {
    for (const k of ["roads", "water", "parks", "buildings"] as const) {
      out.layers[k] = { type: "FeatureCollection", features: raw.features.filter((f: GeoJSON.Feature) => f.properties?.layer === k) };
    }
  }
  return out;
}

export async function fetchBasemap(): Promise<Basemap> {
  if (isMock) return mockBasemap();
  return normalizeBasemap(await api<unknown>("/basemap"));
}

export function buildEdgeIndex(fc: GeoJSON.FeatureCollection): EdgeIndex {
  const byId = new Map<string, EdgeInfo>();
  const byTask = new Map<string, string[]>();
  for (const f of fc.features) {
    const p = (f.properties ?? {}) as Record<string, unknown>;
    const id = String(p.id ?? "");
    if (!id) continue;
    let geom: LonLat[] = [];
    if (f.geometry?.type === "LineString") geom = f.geometry.coordinates as LonLat[];
    else if (f.geometry?.type === "MultiLineString") geom = (f.geometry.coordinates as LonLat[][]).flat();
    const taskId = p.task_id ? String(p.task_id) : null;
    byId.set(id, {
      id, name: (p.name as string) ?? null, slope: Number(p.slope_pct ?? 0), length: Number(p.length_m ?? 0),
      taskId, required: Boolean(p.required), geom,
    });
    if (taskId) {
      const l = byTask.get(taskId);
      if (l) l.push(id); else byTask.set(taskId, [id]);
    }
  }
  return { byId, byTask };
}

export async function fetchEdges(): Promise<EdgeIndex> {
  const fc = isMock ? mockEdges() : await api<GeoJSON.FeatureCollection>("/graph/edges");
  return buildEdgeIndex(fc);
}
