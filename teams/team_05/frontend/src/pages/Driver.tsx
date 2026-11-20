import type { GeoJSONSource } from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { completeTask, fetchDriver, fetchTasks, reportProblem } from "../api/commands";
import type { DriverView, ProblemKind, Task, Vehicle } from "../api/types";
import ElevationProfile from "../components/ElevationProfile";
import { IconAlert, IconBack, IconChevron, IconLocate, IconMinus, IconPlus, IconPlow, IconWarn } from "../components/Icons";
import { ConnBadge, ToastView } from "../components/MapOverlays";
import { analyze } from "../lib/driver";
import { fmtClock, fmtKm, plural } from "../lib/format";
import { VehicleMarkers } from "../map/markers";
import { useMap } from "../map/useMap";
import { usePois } from "../map/pois";
import { useOverlays } from "../map/useOverlays";
import { showToast, startData, useStore } from "../state/store";

const PROBLEMS = ["Поломка", "Не проехать", "Застрял", "Участок уже почищен", "Другое"] as const;
type Problem = (typeof PROBLEMS)[number];

function useDriverData(vid: string) {
  const [dv, setDv] = useState<DriverView | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  useEffect(() => {
    let alive = true;
    const poll = () => fetchDriver(vid).then((d) => alive && setDv(d)).catch(() => {});
    const pollTasks = () => fetchTasks().then((t) => alive && setTasks(t)).catch(() => {});
    poll(); pollTasks();
    const a = window.setInterval(poll, 2000), b = window.setInterval(pollTasks, 8000);
    return () => { alive = false; window.clearInterval(a); window.clearInterval(b); };
  }, [vid]);
  return { dv, tasks };
}

export default function Driver() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const vid = params.get("v") ?? "П-01";
  useEffect(() => startData(), []);
  const basemap = useStore((s) => s.basemap);
  const sim = useStore((s) => s.sim);
  const edges = useStore((s) => s.edges);
  const { dv, tasks } = useDriverData(vid);

  const vehicle: Vehicle | undefined = sim?.vehicles.find((v) => v.id === vid) ?? dv?.vehicle;
  const task = dv?.current_task ?? null;
  const info = useMemo(() => (vehicle ? analyze(vehicle, edges, task, dv?.next_tasks ?? []) : null), [vehicle, edges, task, dv]);

  const container = useRef<HTMLDivElement>(null);
  const { map, ready } = useMap(container, basemap);
  useEffect(() => {
    if (!map || !container.current) return;
    const ro = new ResizeObserver(() => map.resize());
    ro.observe(container.current);
    map.resize();
    return () => ro.disconnect();
  }, [map]);
  usePois(map, ready, basemap);
  const own = useMemo(() => (vehicle ? [vehicle] : []), [vehicle]);
  useOverlays(map, ready, { vehicles: [], edges, proposal: null, planId: sim?.plan_id ?? null, showDone: false });

  // own upcoming route (backend geojson, ~3 km): service blue, deadhead orange dashed
  useEffect(() => {
    if (!map || !ready) return;
    const fc = dv?.route_geojson ?? { type: "FeatureCollection", features: [] };
    const src = map.getSource("drv-route") as GeoJSONSource | undefined;
    if (src) { src.setData(fc as GeoJSON.FeatureCollection); return; }
    map.addSource("drv-route", { type: "geojson", data: fc as GeoJSON.FeatureCollection });
    const lay = { "line-cap": "round", "line-join": "round" } as const;
    const w = ["interpolate", ["linear"], ["zoom"], 13, 3, 16, 7, 18, 12] as never;
    map.addLayer({ id: "drv-casing", type: "line", source: "drv-route", layout: lay,
      paint: { "line-color": "#FFFFFF", "line-width": ["interpolate", ["linear"], ["zoom"], 13, 6, 16, 12, 18, 18] as never } });
    map.addLayer({ id: "drv-dead", type: "line", source: "drv-route", filter: ["!=", ["get", "kind"], "service"], layout: { "line-join": "round" },
      paint: { "line-color": "#F79009", "line-width": w, "line-dasharray": [1.5, 1] } });
    map.addLayer({ id: "drv-svc", type: "line", source: "drv-route", filter: ["==", ["get", "kind"], "service"], layout: lay,
      paint: { "line-color": "#2A62F5", "line-width": w } });
  }, [map, ready, dv?.route_geojson]);

  // marker + follow
  const markers = useRef<VehicleMarkers | null>(null);
  useEffect(() => {
    if (!map || !ready) return;
    const mk = new VehicleMarkers(map);
    markers.current = mk;
    return () => { mk.destroy(); markers.current = null; };
  }, [map, ready]);
  const pos = vehicle?.position;
  const [follow, setFollow] = useState(true);
  const followRef = useRef(true);
  followRef.current = follow;
  const zoomRef = useRef(16.4);
  // manual pan / pinch / wheel zoom (events with originalEvent) stops auto-follow
  useEffect(() => {
    if (!map) return;
    const stop = (e: { originalEvent?: unknown }) => {
      if (!e.originalEvent) return;
      followRef.current = false;
      setFollow(false);
    };
    map.on("dragstart", stop);
    map.on("zoomstart", stop);
    return () => { map.off("dragstart", stop); map.off("zoomstart", stop); };
  }, [map]);
  useEffect(() => { zoomRef.current = 16.4; setFollow(true); }, [vid]);
  const PAD = { top: 190, bottom: 340, left: 0, right: 0 };
  useEffect(() => {
    if (vehicle) markers.current?.update([vehicle], vid);
    if (map && ready && pos && follow) {
      map.easeTo({
        center: [pos.lon, pos.lat], zoom: zoomRef.current, duration: 500, easing: (t) => t, padding: PAD,
        ...(pos.heading_deg != null ? { bearing: pos.heading_deg } : {}),
      });
    }
  }, [map, ready, vehicle, pos?.lon, pos?.lat, follow]); // eslint-disable-line react-hooks/exhaustive-deps
  const zoomBy = (d: number) => {
    if (!map) return;
    const z = Math.max(map.getMinZoom(), Math.min(map.getMaxZoom(), map.getZoom() + d));
    zoomRef.current = z;
    if (followRef.current && pos) map.easeTo({ center: [pos.lon, pos.lat], zoom: z, duration: 250, padding: PAD });
    else map.easeTo({ zoom: z, duration: 250 });
  };
  const recenter = () => { if (map) zoomRef.current = map.getZoom(); setFollow(true); };
  const [switcher, setSwitcher] = useState(false);
  const withQ = (path: string, v?: string) => {
    const q = new URLSearchParams(window.location.search);
    if (v) q.set("v", v); else q.delete("v");
    const s = q.toString();
    return path + (s ? `?${s}` : "");
  };

  // plan-change toast: "+N участков"
  const prevPlan = useRef<{ plan: string | null; ids: Set<string> } | null>(null);
  useEffect(() => {
    if (!vehicle) return;
    const ids = new Set(vehicle.route.filter((s) => !s.done && s.kind === "service" && s.task_id).map((s) => s.task_id!));
    const prev = prevPlan.current;
    if (prev && sim?.plan_id && prev.plan && prev.plan !== sim.plan_id) {
      let added = 0;
      ids.forEach((i) => { if (!prev.ids.has(i)) added++; });
      if (added > 0) showToast(`Маршрут изменён: +${added} ${plural(added, "участок", "участка", "участков")}`, "info", 7000);
      else if (prev.ids.size !== ids.size) showToast("Маршрут изменён", "info", 7000);
    }
    prevPlan.current = { plan: sim?.plan_id ?? null, ids };
  }, [vehicle, sim?.plan_id]);

  const [sheet, setSheet] = useState(false);
  const planIds = new Set<string>();
  vehicle?.route.forEach((s) => { if (s.kind === "service" && s.task_id) planIds.add(s.task_id); });
  const st = sim?.task_statuses ?? {};
  const doneN = [...planIds].filter((id) => (st[id] ?? tasks.find((t) => t.id === id)?.status) === "done").length;
  const n = planIds.size;
  const k = Math.min(n, doneN + 1);
  const pct = n ? Math.min(100, (doneN / n) * 100) : 0;

  const broken = vehicle?.status === "broken";
  const servicing = info?.step?.kind === "service";
  const passesLeft = task ? Math.max(1, task.passes - task.passes_done) : 1;
  const title = broken ? "Машина на ремонте" : !info?.step ? "Маршрут завершён"
    : servicing ? `Чистите · ${passesLeft} ${plural(passesLeft, "проход", "прохода", "проходов")}` : "Перегон";

  const fail = (e: Error) => showToast(`Ошибка: ${e.message}`, "danger");
  const done = () => {
    const id = task?.id ?? vehicle?.current_task_id;
    if (!id) return;
    completeTask(id, vid).then(() => showToast("Участок отмечен как готовый", "success")).catch(fail);
  };
  const KINDS: Record<Problem, ProblemKind> = {
    "Поломка": "breakdown", "Не проехать": "blocked", "Застрял": "stuck", "Участок уже почищен": "already_clean", "Другое": "other",
  };
  const MSG: Record<ProblemKind, string> = {
    breakdown: "Диспетчер уведомлён, маршрут будет перестроен", stuck: "Диспетчер уведомлён, маршрут будет перестроен",
    blocked: "Участок пропущен, маршрут будет перестроен", already_clean: "Участок отмечен как готовый",
    other: "Сообщение отправлено диспетчеру",
  };
  const report = (p: Problem) => {
    setSheet(false);
    const kind = KINDS[p];
    reportProblem(vid, kind).then(() => showToast(MSG[kind], kind === "already_clean" ? "success" : "info")).catch(fail);
  };

  return (
    <div className={`page driver${broken ? " is-broken" : ""}`}>
      <div ref={container} className="map" />
      <div className="dcard panel">
        <span className={`dicon ${broken ? "red" : servicing ? "" : "dead"}`}>
          {broken ? <IconPlow /> : (
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"
              style={{ transform: `rotate(${Math.round(info?.turn ?? 0)}deg)`, transition: "transform .3s" }}><path d="M12 20V5M5.5 11.5 12 5l6.5 6.5" /></svg>
          )}
        </span>
        <div className="dmain">
          <div className="dkind">{title}</div>
          <div className="dstreet">{info?.street ?? (vehicle ? "—" : "Загрузка…")}</div>
          {!broken && info && info.step && (
            <div className="dsub">{fmtKm(info.remainingKm)} {servicing ? "до конца участка" : "перегона"}{info.nextStreet ? ` · далее ${info.nextStreet}` : ""}</div>
          )}
          {broken && <div className="dsub">Ожидает эвакуатор</div>}
        </div>
        {!broken && info?.warn && (
          <div className="warn-chip"><IconWarn />{info.warn.down ? "Спуск" : "Подъём"} {info.warn.pct}% {info.warn.now ? "сейчас" : `через ${info.warn.dist} м`}</div>
        )}
      </div>

      <button className="dback" onClick={() => nav(withQ("/dispatcher"))}><IconBack />Диспетчер</button>
      <div className="dzoom">
        <div className="panel group">
          <button aria-label="Приблизить" onClick={() => zoomBy(1)}><IconPlus /></button>
          <i />
          <button aria-label="Отдалить" onClick={() => zoomBy(-1)}><IconMinus /></button>
        </div>
        <div className="panel group">
          <button aria-label="Следовать за машиной" className={follow ? "on" : ""} onClick={recenter}><IconLocate /></button>
        </div>
      </div>

      <div className="dsheet panel">
        <div className="dsheet-top">
          <button className="vsw" onClick={() => setSwitcher(true)}><b>{vid} · участок {k} из {n}</b><IconChevron /></button>
          <span className="muted">закончим ≈ {info?.finishT != null ? fmtClock(info.finishT) : "—"}</span>
        </div>
        <div className="bar"><i style={{ width: `${pct}%` }} /></div>
        <ElevationProfile profile={info?.profile ?? null} />
        {info?.next && (
          <div className="dnext">
            <i className={`mk ${info.next.deadhead ? "dash" : "solid"}`} />
            <span>{info.next.text}</span>
          </div>
        )}
        <div className="dbtns">
          <button className="btn gray big" onClick={() => setSheet(true)}>Проблема</button>
          {!broken && <button className="btn primary big" disabled={!task && !vehicle?.current_task_id} onClick={done}>Участок готов</button>}
        </div>
      </div>

      {switcher && (
        <div className="scrim" onClick={() => setSwitcher(false)}>
          <div className="psheet panel" onClick={(e) => e.stopPropagation()}>
            <div className="psheet-title">Машина</div>
            {(sim?.vehicles ?? []).map((v) => (
              <button key={v.id} className="menu-item big" style={v.id === vid ? { color: "#2A62F5", fontWeight: 700 } : undefined}
                onClick={() => { setSwitcher(false); if (v.id !== vid) nav(withQ("/driver", v.id)); }}>
                {v.id} · {v.name}
              </button>
            ))}
            <button className="btn gray big" onClick={() => setSwitcher(false)}>Отмена</button>
          </div>
        </div>
      )}
      {sheet && (
        <div className="scrim" onClick={() => setSheet(false)}>
          <div className="psheet panel" onClick={(e) => e.stopPropagation()}>
            <div className="psheet-title"><IconAlert />Что случилось?</div>
            {PROBLEMS.map((p) => <button key={p} className="menu-item big" onClick={() => report(p)}>{p}</button>)}
            <button className="btn gray big" onClick={() => setSheet(false)}>Отмена</button>
          </div>
        </div>
      )}
      <ToastView />
      <ConnBadge />
    </div>
  );
}
