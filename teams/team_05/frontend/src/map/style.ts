import type { StyleSpecification } from "maplibre-gl";
import type { Basemap } from "../api/types";
import { overlayLayers, overlaySources } from "./layers";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type L = any;
const z = (...stops: number[]): L => ["interpolate", ["linear"], ["zoom"], ...stops];
const MAJOR: L = ["match", ["get", "highway"], ["primary", "secondary", "trunk"], true, false];
const MINOR: L = ["!", MAJOR];
// tiles generated up to the map max zoom, minimal simplification, so nothing disappears when zoomed in
const GJ = { maxzoom: 19, tolerance: 0.1, buffer: 128 };
const round = { "line-cap": "round", "line-join": "round" };

/** Own style built from /api/basemap GeoJSON. No external tiles, no glyphs (no symbol layers). */
export function buildStyle(b: Basemap): StyleSpecification {
  const layers: L[] = [
    { id: "bg", type: "background", paint: { "background-color": "#F3F1ED" } },
    { id: "parks", type: "fill", source: "parks", paint: { "fill-color": "#D5EAC4" } },
    { id: "water", type: "fill", source: "water", paint: { "fill-color": "#AAD3F0" } },
    { id: "buildings", type: "fill", source: "buildings", minzoom: 14, paint: { "fill-color": "#E9E5DE" } },
    { id: "road-casing", type: "line", source: "roads", filter: MINOR, layout: round,
      paint: { "line-color": "#D8D2C7", "line-width": z(11, 1.2, 14, 4, 16, 8, 18, 18) } },
    { id: "major-casing", type: "line", source: "roads", filter: MAJOR, layout: round,
      paint: { "line-color": "#E3C063", "line-width": z(11, 2.5, 14, 6, 16, 11, 18, 24) } },
    { id: "road-fill", type: "line", source: "roads", filter: MINOR, layout: round,
      paint: { "line-color": "#FFFFFF", "line-width": z(11, 0.6, 14, 2.4, 16, 5.5, 18, 14) } },
    { id: "major-fill", type: "line", source: "roads", filter: MAJOR, layout: round,
      paint: { "line-color": "#FFE594", "line-width": z(11, 1.4, 14, 3.6, 16, 8.5, 18, 20) } },
    ...overlayLayers(),
  ];
  return {
    version: 8,
    sources: {
      roads: { type: "geojson", data: b.layers.roads, ...GJ },
      water: { type: "geojson", data: b.layers.water, ...GJ },
      parks: { type: "geojson", data: b.layers.parks, ...GJ },
      buildings: { type: "geojson", data: b.layers.buildings, ...GJ },
      ...overlaySources(),
    },
    layers,
  } as StyleSpecification;
}
