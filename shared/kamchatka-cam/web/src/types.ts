export type Scene = {
  falling_snow: string; intensity: string; visibility: string; snow_on_ground: string;
  large_snowdrifts: string; road_coverage: string; evidence: string; format_valid: boolean;
  model: string; prompt_version: string; snow_source?: 'scene' | 'snow' | null;
  details?: { blowing_snow: string; snowdrift_on_road: string; slush_on_road: string; road_obstruction: string;
    air_view: string; ground_view: string; road_view: string; roadside_view: string } | null;
};
export type Camera = {
  id: string; name: string; area: string; road: boolean; coordinates: [number, number] | null;
  analyzed_frame_id: string | null; analyzed_frame_at: string | null;
  location_note: string | null; status: string; last_frame: string | null; last_poll: string | null; source_at: string | null; frame_id: string | null; scene: Scene | null;
};
export type EventItem = {
  id: string; kind: string; name: string; area: string; severity: 'watch' | 'urgent'; reason: string;
  state: string; version: number; evidence_stale: boolean; opened_at: string; last_seen: string; ended_at: string | null;
  first_frame: string; latest_frame: string;
};
export type FeedbackVerdict = 'confirmed' | 'false_alarm' | 'already_working';
export type ObservedFrame = { id: string; observed_at: string };
export type SignalReview = {
  before: ObservedFrame | null;
  current: ObservedFrame | null;
  after: (ObservedFrame & { actor: string }) | null;
  candidate: ObservedFrame | null;
  feedback: { verdict: FeedbackVerdict; updated_at: string } | null;
};
export type Inspection = {
  event_id: string; name: string; area: string; severity: 'watch' | 'urgent'; last_seen: string; factors: string[];
};
export type ActionItem = { request_id: string; actor: string; state: string; note: string; created_at: string };
export type Period = {
  at: string; horizon: 'daily' | 'hourly' | 'three_hourly'; probability: number | null; snow_mm: number | null;
  snow: boolean; wind_mps: number | null; temperature_c: number | null;
};
export type Warning = { point: string; payload: Period & { start: string; end: string } };
export type Summary = {
  day: string; payload: {
    start: string; end: string; events: Pick<EventItem, 'id' | 'reason' | 'state' | 'name'>[];
    stale_cameras: string[]; warnings: Warning[];
    actions: (Pick<ActionItem, 'state' | 'note' | 'created_at'> & { name: string })[];
  };
};
export type Delivery = {
  id: number; kind: string; subject: string; body: string; stage: string; attempts: number;
  error: string | null; created_at: string;
};
export type Dashboard = {
  mode: 'live' | 'preview' | 'demo' | 'replay'; run_id: string; role: 'viewer' | 'operator';
  cameras: Camera[]; events: EventItem[]; forecasts: { point: string; fetched_at: string; periods: Period[] }[];
  warnings: Warning[]; summaries: Summary[]; deliveries: Delivery[];
  checks: { name: string; status: string; observed_at: string }[];
  inspection_queue: Inspection[];
  feedback_counts: Partial<Record<FeedbackVerdict, number>>;
  queue: { pending: number; oldest: string | null };
};
