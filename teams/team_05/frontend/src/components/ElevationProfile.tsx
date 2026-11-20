import { fmtNum } from "../lib/format";

export interface Profile { pts: [number, number][]; slopes: number[]; len: number }

export default function ElevationProfile({ profile }: { profile: Profile | null }) {
  const W = 340, H = 64, PX = 4, PY = 8;
  if (!profile || profile.pts.length < 2 || profile.len <= 0) return <div className="profile empty-profile">Профиль высот недоступен</div>;
  const ys = profile.pts.map((p) => p[1]);
  const lo = Math.min(...ys), hi = Math.max(...ys), span = Math.max(hi - lo, 4);
  const X = (d: number) => PX + (d / profile.len) * (W - 2 * PX);
  const Y = (e: number) => H - PY - ((e - lo) / span) * (H - 2 * PY);
  const line = profile.pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join(" ");
  const area = `${line} L${X(profile.len).toFixed(1)} ${H} L${X(0).toFixed(1)} ${H} Z`;
  return (
    <div className="profile">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Профиль высот">
        <path d={area} fill="#2A62F5" fillOpacity="0.1" />
        {profile.pts.slice(1).map((p, i) => {
          const a = profile.pts[i];
          const steep = Math.abs(profile.slopes[i]) > 4;
          return <line key={i} x1={X(a[0])} y1={Y(a[1])} x2={X(p[0])} y2={Y(p[1])} stroke={steep ? "#B54708" : "#2A62F5"}
            strokeWidth="3" strokeLinecap="round" />;
        })}
        <circle cx={X(0)} cy={Y(profile.pts[0][1])} r="5" fill="#fff" stroke="#2A62F5" strokeWidth="3" />
      </svg>
      <div className="profile-axis"><span>сейчас</span><span>{fmtNum(profile.len / 1000)} км · перепад {Math.round(hi - lo)} м</span></div>
    </div>
  );
}
