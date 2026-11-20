import { useEffect, useState } from "react";
import type { Metrics, ReplanProposal, SimEvent } from "../api/types";
import { fmtClock, fmtDur, fmtKm, fmtNum, sections } from "../lib/format";
import { vehicleColor } from "../lib/vehicle";
import { applyProposal, rejectProposal } from "../api/commands";
import { showToast } from "../state/store";

function MetricRow({ label, a, b, fmt }: { label: string; a: number; b: number; fmt: (x: number) => string }) {
  const better = b < a - 1e-6, worse = b > a + 1e-6;
  return (
    <div className="mrow">
      <span className="muted">{label}</span>
      <span className="mvals">{fmt(a)} <span className="arrow">→</span> <b className={better ? "good" : worse ? "bad" : ""}>{fmt(b)}</b></span>
    </div>
  );
}
const dm = (x: Metrics, y: Metrics) => [x, y] as const;

export default function ProposalCard({ proposal, events, receivedAt, total }: {
  proposal: ReplanProposal; events: SimEvent[]; receivedAt: number; total: number;
}) {
  const [now, setNow] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  useEffect(() => { const i = window.setInterval(() => setNow(Date.now()), 200); return () => window.clearInterval(i); }, []);

  const ev = events.find((e) => e.id === proposal.reason_event_id);
  const kind = ev?.type === "breakdown" ? "Поломка" : ev?.type === "new_request" ? "Новая заявка" : "Перепланирование";
  const [b, a] = dm(proposal.metrics_before, proposal.metrics_after);

  let left: number | null = null;
  if (proposal.auto_apply_in_s != null) left = Math.max(0, proposal.auto_apply_in_s - (now - receivedAt) / 1000);
  else if (proposal.auto_apply_at != null) left = Math.max(0, proposal.auto_apply_at - now / 1000);
  const fill = left !== null && total > 0 ? Math.min(100, Math.max(0, (1 - left / total) * 100)) : 0;

  const act = async (fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    try { await fn(); } catch (e) { showToast(`Ошибка: ${(e as Error).message}`, "danger"); } finally { setBusy(false); }
  };

  return (
    <div className="panel proposal">
      <div className="prop-body">
        <span className={`badge ${ev?.type === "breakdown" ? "danger" : ""}`}>{kind}{ev ? ` · ${fmtClock(ev.t)}` : ""}</span>
        <div className="prop-title">{ev?.text ?? proposal.reason_text}</div>
        {ev && <p className="prop-desc">{proposal.reason_text}</p>}

        <div className="sect-label">Изменения в маршрутах</div>
        {proposal.diffs.filter((d) => d.added_task_ids.length || d.removed_task_ids.length).map((d) => (
          <div className="diff" key={d.vehicle_id}>
            <span className="avatar sm" style={{ background: vehicleColor(d.vehicle_id) }}>{d.vehicle_id}</span>
            <span className="diff-main">
              <b>{d.vehicle_id}</b>
              <span className="muted">
                {d.added_task_ids.length > 0 && `+${sections(d.added_task_ids.length)}`}
                {d.removed_task_ids.length > 0 && `−${sections(d.removed_task_ids.length)} (передаются)`}
                {d.added_task_ids.length > 0 && ` · ${d.delta_km >= 0 ? "+" : "−"}${fmtKm(Math.abs(d.delta_km))}`}
                {d.new_finish_t != null && ` · до ${fmtClock(d.new_finish_t)}${Math.abs(d.delta_finish_min) >= 1 ? ` (${d.delta_finish_min > 0 ? "+" : "−"}${Math.round(Math.abs(d.delta_finish_min))} мин)` : ""}`}
              </span>
            </span>
          </div>
        ))}

        <div className="sect-label">Остаток работы</div>
        <MetricRow label="Холостой пробег" a={b.deadhead_km} b={a.deadhead_km} fmt={(x) => fmtKm(x)} />
        <MetricRow label="Время до конца" a={b.total_time_min} b={a.total_time_min} fmt={fmtDur} />
        <MetricRow label="Топливо" a={b.fuel_l} b={a.fuel_l} fmt={(x) => `${fmtNum(x)} л`} />
      </div>
      <div className="prop-actions">
        <button className="btn primary big fillbtn" disabled={busy} onClick={() => act(() => applyProposal(proposal.id))}>
          <span className="fillbar" style={{ width: `${fill}%` }} />
          <span className="fill-label">{left !== null ? `Применить · авто через ${Math.ceil(left)} с` : "Применить"}</span>
        </button>
        <button className="btn ghost big" disabled={busy} onClick={() => act(() => rejectProposal(proposal.id))}>Отклонить</button>
      </div>
    </div>
  );
}
