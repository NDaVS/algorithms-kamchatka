import { useMemo, useState } from "react";
import type { EdgeIndex } from "../api/data";
import type { SimState, Vehicle } from "../api/types";
import { fmtClock, fmtKm } from "../lib/format";
import { statusText, statusColor, vehicleColor } from "../lib/vehicle";
import { IconPhone, IconSearch } from "./Icons";

type Filter = "all" | "plow" | "truck";

function subtitle(v: Vehicle, edges: EdgeIndex | null): string {
  if (v.status === "broken") return "Остановлена, ждёт эвакуации";
  if (v.status === "at_depot") return "База №1";
  const name = v.position?.edge_id ? edges?.byId.get(v.position.edge_id)?.name : null;
  if (name) return name;
  return v.distance_km > 0 ? `Пройдено ${fmtKm(v.distance_km)}` : "Готов к выезду";
}

export function finishTime(sim: SimState): number | null {
  let max = -1;
  for (const v of sim.vehicles) {
    if (v.kind !== "plow" || v.status === "broken" || !v.route.length) continue;
    if (v.route.every((s) => s.done)) continue;
    max = Math.max(max, v.route[v.route.length - 1].t_end);
  }
  if (max < 0) return sim.plan_metrics ? sim.t + sim.plan_metrics.total_time_min * 60 : null;
  return Math.max(max, sim.t);
}

export default function FleetPanel({ sim, edges, selected, onSelect, onDriver }: {
  sim: SimState | null; edges: EdgeIndex | null; selected: string | null; onSelect: (id: string) => void; onDriver?: (id: string) => void;
}) {
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const vehicles = sim?.vehicles ?? [];
  const plows = vehicles.filter((v) => v.kind === "plow").length;
  const trucks = vehicles.length - plows;

  const list = useMemo(() => {
    const s = q.trim().toLowerCase();
    return vehicles.filter((v) => {
      if (filter !== "all" && v.kind !== filter) return false;
      if (!s) return true;
      return v.id.toLowerCase().includes(s) || v.name.toLowerCase().includes(s) || subtitle(v, edges).toLowerCase().includes(s);
    });
  }, [vehicles, q, filter, edges]);

  const sum = sim?.tasks_summary;
  const pct = sum && sum.total_km > 0 ? Math.min(100, (sum.done_km / sum.total_km) * 100) : 0;
  const fin = sim ? finishTime(sim) : null;
  const dh = sim?.comparison?.deadhead_pct;

  return (
    <div className="panel fleet">
      <div className="fleet-head">
        <div className="title">Снегоплан</div>
        <div className="muted">смена 06:00–14:00</div>
      </div>
      <label className="search">
        <IconSearch />
        <input placeholder="Машина или улица" value={q} onChange={(e) => setQ(e.target.value)} />
      </label>
      <div className="chips">
        <button className={`chip ${filter === "all" ? "on" : ""}`} onClick={() => setFilter("all")}>Все · {vehicles.length}</button>
        <button className={`chip ${filter === "plow" ? "on" : ""}`} onClick={() => setFilter("plow")}>Плуги · {plows}</button>
        <button className={`chip ${filter === "truck" ? "on" : ""}`} onClick={() => setFilter("truck")}>Самосвалы · {trucks}</button>
      </div>
      <div className="fleet-list">
        {!sim && <div className="empty">Ожидаем данные…</div>}
        {sim && list.length === 0 && <div className="empty">Ничего не найдено</div>}
        {list.map((v) => (
          <button key={v.id} className={`row ${selected === v.id ? "sel" : ""}`} onClick={() => onSelect(v.id)}>
            <span className={`avatar ${v.kind === "truck" ? "sq" : ""}`} style={{ background: v.status === "broken" ? "#E5322D" : vehicleColor(v.id) }}>{v.id}</span>
            <span className="row-main">
              <span className="row-top">
                <span className="row-id">{v.id}</span>
                <span className="row-status" style={{ color: statusColor(v.status) }}>{statusText(v)}</span>
              </span>
              <span className="row-sub">{subtitle(v, edges)}</span>
            </span>
            {onDriver && (
              <span role="button" tabIndex={0} className="row-phone" title="Экран водителя" aria-label="Экран водителя"
                onClick={(e) => { e.stopPropagation(); onDriver(v.id); }}><IconPhone /></span>
            )}
          </button>
        ))}
      </div>
      <div className="fleet-foot">
        <div className="stat-row"><span>Холостой пробег</span><b>{fmtKm(sim?.metrics.deadhead_km ?? 0)}</b></div>
        {(sim?.metrics.truck_empty_km ?? 0) > 0 && (
          <div className="stat-row"><span>Пустой пробег самосвалов</span><b>{fmtKm(sim?.metrics.truck_empty_km ?? 0)}</b></div>
        )}
        <div className="stat-row"><span>Почищено улиц</span><b>{Math.round(pct)}%</b></div>
        <div className="bar"><i style={{ width: `${pct}%` }} /></div>
        <div className="stat-row"><span>Закончим примерно</span><b>{fin !== null ? fmtClock(fin) : "—"}</b></div>
        {dh !== undefined && Math.abs(dh) >= 0.5 && (
          <div className="small muted">
            {dh < 0 ? `Холостого пробега на ${Math.round(-dh)}% меньше, чем в базовом плане`
              : `Холостого пробега на ${Math.round(dh)}% больше, чем в базовом плане`}
          </div>
        )}
      </div>
    </div>
  );
}
