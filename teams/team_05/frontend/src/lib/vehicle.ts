import type { Vehicle, VehicleStatus } from "../api/types";

const PLOW_COLORS = ["#2A62F5", "#8A4DFF", "#0E9F6E", "#D6336C", "#0B8FA6", "#B7791F"];
export const TRUCK_COLOR = "#3B4452";
export const DEADHEAD_COLOR = "#F2731D";
export const DANGER = "#E5322D";

export const isTruckId = (id: string) => id.startsWith("С");

export function vehicleColor(id: string): string {
  if (isTruckId(id)) return TRUCK_COLOR;
  const n = parseInt(id.replace(/\D+/g, ""), 10);
  return PLOW_COLORS[((Number.isFinite(n) ? n : 1) - 1 + PLOW_COLORS.length * 10) % PLOW_COLORS.length];
}

export const STATUS_TEXT: Record<VehicleStatus, string> = {
  idle: "Ожидает", servicing: "Чистит", deadheading: "Перегон", loaded: "Везёт снег",
  returning: "Возвращается", broken: "Поломка", at_depot: "На базе", finished: "Смена завершена",
};
export function statusColor(s: VehicleStatus): string {
  switch (s) {
    case "broken": return "#B42318";
    case "servicing": return "#2A62F5";
    case "deadheading": case "returning": return "#C2540A";
    case "loaded": return "#3B4452";
    case "at_depot": case "finished": return "#12813F";
    default: return "#6B6B6B";
  }
}

/** Russian status label; trucks have their own wording (empty run / unloading). */
export function statusText(v: Pick<Vehicle, "kind" | "status">): string {
  if (v.kind === "truck") {
    if (v.status === "deadheading") return "Едет пустой";
    if (v.status === "servicing") return "Выгрузка";
  }
  return STATUS_TEXT[v.status];
}
