import { useEffect } from "react";
import { Marker, type Map as MLMap } from "maplibre-gl";
import type { Basemap } from "../api/types";

const ICON: Record<string, string> = {
  dump: '<path d="M12 6 19 18H5z" fill="#fff"/><path d="M12 9v6M9.4 10.5l5.2 3M14.6 10.5l-5.2 3" stroke="#475467" stroke-width="1.2" fill="none"/>',
  depot: '<path d="M12 6 19 12v7H5v-7z" fill="#fff"/>',
};

/** Static POI markers (depot, snow dump): dark gray circle with white icon + label. */
export function usePois(map: MLMap | null, ready: boolean, basemap: Basemap | null) {
  useEffect(() => {
    if (!map || !ready || !basemap?.pois) return;
    const ms = basemap.pois.map((p) => {
      const el = document.createElement("div");
      el.style.cssText = "display:flex;flex-direction:column;align-items:center;pointer-events:none;";
      el.innerHTML = `<svg width="30" height="30" viewBox="0 0 24 24"><circle cx="12" cy="12" r="11" fill="#344054" stroke="#fff" stroke-width="1.5"/>${ICON[p.kind] ?? ""}</svg>` +
        `<span style="margin-top:2px;padding:1px 6px;border-radius:6px;background:rgba(255,255,255,.92);color:#344054;font:600 11px system-ui,sans-serif;white-space:nowrap;box-shadow:0 1px 2px rgba(0,0,0,.2)">${p.name}</span>`;
      return new Marker({ element: el, anchor: "top" }).setLngLat([p.lon, p.lat]).addTo(map);
    });
    return () => ms.forEach((m) => m.remove());
  }, [map, ready, basemap]);
}
