const pad = (n: number) => String(n).padStart(2, "0");

/** Sim seconds since 06:00 -> "HH:MM". */
export function fmtClock(t: number): string {
  const m = Math.floor(Math.max(0, t) / 60) + 360;
  return `${pad(Math.floor(m / 60) % 24)}:${pad(m % 60)}`;
}
export const fmtKm = (km: number, digits = 1) => `${km.toFixed(digits).replace(".", ",")} км`;
export const fmtNum = (x: number, digits = 1) => x.toFixed(digits).replace(".", ",");
export function fmtDur(min: number): string {
  const m = Math.round(Math.max(0, min));
  const h = Math.floor(m / 60);
  return h > 0 ? `${h} ч ${m % 60} мин` : `${m} мин`;
}
export function plural(n: number, one: string, few: string, many: string): string {
  const a = Math.abs(n) % 100, b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}
export const sections = (n: number) => `${n} ${plural(n, "участок", "участка", "участков")}`;
