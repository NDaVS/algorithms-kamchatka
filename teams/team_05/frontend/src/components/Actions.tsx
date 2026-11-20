import { useState, type ReactNode } from "react";
import { breakdown } from "../api/commands";
import type { Vehicle } from "../api/types";
import { vehicleColor } from "../lib/vehicle";
import { showToast } from "../state/store";

const fail = (e: Error) => showToast(`Ошибка: ${e.message}`, "danger");

export default function Actions({ vehicles, selected, picking, onPick, onRandom, children }: {
  children?: ReactNode; vehicles: Vehicle[]; selected: string | null; picking: boolean; onPick: (on: boolean) => void; onRandom: () => void;
}) {
  const [menu, setMenu] = useState(false);
  const plows = vehicles.filter((v) => v.kind === "plow" && v.status !== "broken");
  const sel = vehicles.find((v) => v.id === selected);
  const onBreak = () => {
    if (sel && sel.kind === "plow" && sel.status !== "broken") { breakdown(sel.id).catch(fail); setMenu(false); }
    else setMenu((m) => !m);
  };
  return (
    <>
      <div className="actions">
        <div className="menu-wrap">
          <button className="btn white danger-text" onClick={onBreak}>Поломка</button>
          {menu && (
            <div className="panel menu">
              <div className="menu-title">Какая машина сломалась?</div>
              {plows.length === 0 && <div className="empty">Нет доступных плугов</div>}
              {plows.map((v) => (
                <button key={v.id} className="menu-item" onClick={() => { setMenu(false); breakdown(v.id).catch(fail); }}>
                  <span className="dot" style={{ background: vehicleColor(v.id) }} />{v.id}
                </button>
              ))}
            </div>
          )}
        </div>
        <button className={`btn white ${picking ? "active" : ""}`} onClick={() => onPick(!picking)}>Новая заявка</button>
        {children}
      </div>
      {picking && (
        <div className="pill hint">
          <span>Нажмите на карту, чтобы выбрать улицу</span>
          <button className="chip small" onClick={onRandom}>Случайная</button>
          <button className="chip small" onClick={() => onPick(false)}>Отмена</button>
        </div>
      )}
    </>
  );
}
