import { simControl } from "../api/commands";
import type { SimState } from "../api/types";
import { fmtClock } from "../lib/format";
import { showToast } from "../state/store";
import { IconPause, IconPlay } from "./Icons";

export default function ControlsBar({ sim }: { sim: SimState | null }) {
  const running = !!sim?.running;
  const run = (action: "start" | "pause", speed?: number) =>
    simControl(action, speed).catch((e: Error) => showToast(`Ошибка: ${e.message}`, "danger"));
  return (
    <div className="pill topbar">
      <button className="round-btn primary" aria-label={running ? "Пауза" : "Запустить"}
        onClick={() => run(running ? "pause" : "start")}>{running ? <IconPause /> : <IconPlay />}</button>
      <span className="clock">{sim ? fmtClock(sim.t) : "--:--"}</span>
      <span className="sep" />
      {[1, 10, 60].map((s) => (
        <button key={s} className={`chip small ${sim?.speed === s ? "on" : ""}`} onClick={() => run(running ? "start" : "pause", s)}>×{s}</button>
      ))}
    </div>
  );
}
