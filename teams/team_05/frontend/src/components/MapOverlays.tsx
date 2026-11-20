import type { Map as MLMap } from "maplibre-gl";
import type { Basemap } from "../api/types";
import { useStore } from "../state/store";
import { IconFit, IconMinus, IconPlus } from "./Icons";

export function ZoomControls({ map, basemap, padLeft }: { map: MLMap | null; basemap: Basemap | null; padLeft: number }) {
  return (
    <div className="zoom">
      <div className="panel group">
        <button aria-label="Приблизить" onClick={() => map?.zoomIn()}><IconPlus /></button>
        <i />
        <button aria-label="Отдалить" onClick={() => map?.zoomOut()}><IconMinus /></button>
      </div>
      <div className="panel group">
        <button aria-label="Показать всё" onClick={() => basemap && map?.fitBounds(basemap.bbox, { padding: { top: 40, bottom: 40, right: 40, left: padLeft }, duration: 600 })}>
          <IconFit />
        </button>
      </div>
    </div>
  );
}

export function Legend() {
  return (
    <div className="pill legend">
      <span><i className="lg solid" />Уборка</span>
      <span><i className="lg dash" />Холостой ход</span>
      <span><i className="lg faded" />Пройдено</span>
      <span><i className="lg red" />Снимается с машины</span>
    </div>
  );
}

export function ToastView() {
  const toast = useStore((s) => s.toast);
  if (!toast) return null;
  return <div key={toast.id} className={`toast ${toast.kind}`}>{toast.text}</div>;
}

export function ConnBadge() {
  const connected = useStore((s) => s.connected);
  const starting = useStore((s) => s.starting);
  const hasSim = useStore((s) => s.sim !== null);
  if (connected && !starting && hasSim) return null;
  const text = connected || starting ? "Сервер запускается…" : "Подключение к серверу…";
  return <div className="conn-pill">{text}</div>;
}
