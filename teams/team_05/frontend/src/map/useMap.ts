import { useEffect, useState, type RefObject } from "react";
import { Map as MLMap } from "maplibre-gl";
import type { Basemap } from "../api/types";
import { buildStyle } from "./style";

export function useMap(
  container: RefObject<HTMLDivElement>,
  basemap: Basemap | null,
  opts: { padLeft?: number; zoom?: number } = {}
) {
  const [map, setMap] = useState<MLMap | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!basemap || !container.current) return;
    const pad = 40;
    const m = new MLMap({
      container: container.current,
      style: buildStyle(basemap),
      bounds: basemap.bbox,
      fitBoundsOptions: { padding: { top: pad, bottom: pad, right: pad, left: opts.padLeft ?? pad } },
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
      maxZoom: 19,
      minZoom: 10,
    });
    m.touchZoomRotate.disableRotation();
    m.on("load", () => setReady(true));
    m.on("error", (e) => console.error("[map]", e.error?.message ?? e));
    m.getCanvas().addEventListener("webglcontextlost", (e) => e.preventDefault());
    m.getCanvas().addEventListener("webglcontextrestored", () => m.triggerRepaint());
    setMap(m);
    return () => { m.remove(); setMap(null); setReady(false); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [basemap]);
  return { map, ready };
}
