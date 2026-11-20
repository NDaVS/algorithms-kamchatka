import type { MapMouseEvent } from "maplibre-gl";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { newRequest } from "../api/commands";
import Actions from "../components/Actions";
import ControlsBar from "../components/ControlsBar";
import FleetPanel from "../components/FleetPanel";
import { IconPhone } from "../components/Icons";
import { Legend, ToastView, ZoomControls, ConnBadge } from "../components/MapOverlays";
import ProposalCard from "../components/ProposalCard";
import { VehicleMarkers } from "../map/markers";
import { useMap } from "../map/useMap";
import { usePois } from "../map/pois";
import { useOverlays } from "../map/useOverlays";
import { showToast, startData, useStore } from "../state/store";

const PAD_LEFT = 392;

export default function Dispatcher() {
  useEffect(() => startData(), []);
  const nav = useNavigate();
  const [menu, setMenu] = useState(false);
  const toDriver = (id: string) => {
    const q = new URLSearchParams(window.location.search);
    q.set("v", id);
    nav(`/driver?${q.toString()}`);
  };
  const basemap = useStore((s) => s.basemap);
  const sim = useStore((s) => s.sim);
  const edges = useStore((s) => s.edges);
  const proposal = useStore((s) => s.proposal);
  const proposalAt = useStore((s) => s.proposalAt);
  const proposalTotal = useStore((s) => s.proposalTotal);
  const events = useStore((s) => s.events);

  const [selected, setSelected] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const pickRef = useRef(false);
  pickRef.current = picking;

  const container = useRef<HTMLDivElement>(null);
  const { map, ready } = useMap(container, basemap, { padLeft: PAD_LEFT });
  usePois(map, ready, basemap);
  const vehicles = sim?.vehicles ?? [];

  useOverlays(map, ready, { vehicles, edges, proposal, statuses: sim?.task_statuses, planId: sim?.plan_id ?? null, selected });

  // fit initial view to the actual graph extent (once), leaving room for the left panel
  const fitted = useRef(false);
  useEffect(() => { fitted.current = false; }, [map]);
  useEffect(() => {
    if (!map || !ready || !edges || fitted.current || !edges.byId.size) return;
    let x0 = 180, y0 = 90, x1 = -180, y1 = -90;
    const [bx0, by0, bx1, by1] = basemap?.bbox ?? [-180, -90, 180, 90];
    for (const e of edges.byId.values()) for (const p of e.geom ?? []) {
      if (p[0] < bx0 || p[0] > bx1 || p[1] < by0 || p[1] > by1) continue; // ignore the road to the outside dump
      if (p[0] < x0) x0 = p[0]; if (p[0] > x1) x1 = p[0];
      if (p[1] < y0) y0 = p[1]; if (p[1] > y1) y1 = p[1];
    }
    if (x0 >= x1 || y0 >= y1) return;
    fitted.current = true;
    map.fitBounds([[x0, y0], [x1, y1]], { padding: { top: 70, bottom: 40, right: 40, left: PAD_LEFT }, duration: 0 });
  }, [map, ready, edges]); // eslint-disable-line react-hooks/exhaustive-deps

  // select + fly (vehicles read through a ref: the callback must not capture stale data)
  const vehiclesRef = useRef(vehicles);
  vehiclesRef.current = vehicles;
  const select = useCallback((id: string) => {
    setSelected(id);
    const v = vehiclesRef.current.find((x) => x.id === id);
    if (map && v?.position) {
      map.flyTo({ center: [v.position.lon, v.position.lat], zoom: Math.max(map.getZoom(), 15.5),
        padding: { left: PAD_LEFT, top: 0, right: 0, bottom: 0 }, duration: 800 });
    }
  }, [map]);
  const selectRef = useRef(select);
  selectRef.current = select;

  // markers
  const markers = useRef<VehicleMarkers | null>(null);
  useEffect(() => {
    if (!map || !ready) return;
    const mk = new VehicleMarkers(map, (id) => selectRef.current(id));
    markers.current = mk;
    return () => { mk.destroy(); markers.current = null; };
  }, [map, ready]);
  useEffect(() => { markers.current?.update(vehicles, selected, edges); });

  // pick a location for a new request
  useEffect(() => {
    if (!map || !ready) return;
    const h = (e: MapMouseEvent) => {
      if (!pickRef.current) return;
      setPicking(false);
      newRequest({ lon: e.lngLat.lng, lat: e.lngLat.lat }).catch((err: Error) => showToast(`Ошибка: ${err.message}`, "danger"));
    };
    map.on("click", h);
    return () => { map.off("click", h); };
  }, [map, ready]);
  useEffect(() => { if (map) map.getCanvas().style.cursor = picking ? "crosshair" : ""; }, [map, picking]);

  const random = () => {
    setPicking(false);
    let body = {};
    if (edges) {
      const req = Array.from(edges.byId.values()).filter((e) => e.required);
      if (req.length) body = { edge_id: req[Math.floor(Math.random() * req.length)].id };
    }
    if (!("edge_id" in body) && basemap) {
      const [x0, y0, x1, y1] = basemap.bbox;
      body = { lon: x0 + (x1 - x0) * (0.2 + Math.random() * 0.6), lat: y0 + (y1 - y0) * (0.2 + Math.random() * 0.6) };
    }
    newRequest(body).catch((err: Error) => showToast(`Ошибка: ${err.message}`, "danger"));
  };

  return (
    <div className="page">
      <div ref={container} className="map" />
      {proposal
        ? <ProposalCard key={proposal.id} proposal={proposal} events={events} receivedAt={proposalAt} total={proposalTotal} />
        : <FleetPanel sim={sim} edges={edges} selected={selected} onSelect={select} onDriver={toDriver} />}
      <div className="topbar-wrap" style={{ left: PAD_LEFT }}><ControlsBar sim={sim} /></div>
      <Actions vehicles={vehicles} selected={selected} picking={picking} onPick={setPicking} onRandom={random}>
        <div className="menu-wrap">
          <button className="btn white" onClick={() => setMenu((m) => !m)}><IconPhone />Экран водителя</button>
          {menu && (
            <div className="panel menu drv-menu">
              {vehicles.length === 0 && <div className="empty">Нет машин</div>}
              {vehicles.map((v) => (
                <button key={v.id} className="menu-item" onClick={() => toDriver(v.id)}>{v.id} · {v.name}</button>
              ))}
            </div>
          )}
        </div>
      </Actions>
      <ZoomControls map={map} basemap={basemap} padLeft={PAD_LEFT} />
      <Legend />
      <ToastView />
      <ConnBadge />
    </div>
  );
}
