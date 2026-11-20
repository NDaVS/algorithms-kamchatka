// TS mirror of backend/core/models.py. KEEP IN SYNC. Sim time `t` = seconds since scenario start.
export type LonLat = [number, number];

export type TaskStatus = "pending" | "assigned" | "in_progress" | "done" | "cancelled";
export type VehicleKind = "plow" | "truck";
export type VehicleStatus = "idle" | "servicing" | "deadheading" | "loaded" | "returning" | "broken" | "at_depot" | "finished";
export type StepKind = "service" | "deadhead" | "unload" | "return";
export type EventType =
  | "breakdown" | "new_request" | "task_done" | "plan_applied" | "proposal_created"
  | "proposal_rejected" | "vehicle_at_depot" | "sim_control" | "info";
export type ProposalStatus = "pending" | "applied" | "rejected" | "expired";

export interface Node {
  id: number; lon: number; lat: number; elevation_m: number;
  kind: "junction" | "depot" | "dump"; name: string | null;
}
export interface Edge {
  id: string; u: number; v: number; key: number; length_m: number; slope_pct: number;
  geometry: LonLat[]; name: string | null; highway: string; oneway: boolean; required: boolean;
  street_id: string | null; reverse_edge_id: string | null; task_id: string | null;
}
export interface Task {
  id: string; edge_ids: string[]; u: number; v: number; street_name: string | null;
  length_m: number; passes: number; passes_done: number; status: TaskStatus;
  assigned_vehicle: string | null; priority: number; source: "initial" | "request";
}
export interface Position {
  lon: number; lat: number; edge_id: string | null; offset_m: number; heading_deg: number | null;
}
export interface RouteStep {
  kind: StepKind; edge_id: string | null; task_id: string | null; from_node: number; to_node: number;
  length_m: number; loaded: boolean; t_start: number; t_end: number; fuel_l: number; done: boolean;
}
export interface Vehicle {
  id: string; kind: VehicleKind; name: string; depot_node: number; start_node: number | null;
  service_speed_kmh: number; deadhead_speed_kmh: number; capacity_m3: number;
  status: VehicleStatus; loaded: boolean; position: Position | null; route: RouteStep[];
  route_index: number; current_task_id: string | null; distance_km: number; fuel_used_l: number;
  trail?: [number, number][];   // [lon,lat] path traversed since previous broadcast snapshot (WS only)
}
export interface ObjectiveWeights { deadhead: number; time: number; fuel: number }
export interface Metrics {
  deadhead_km: number; service_km: number; total_time_min: number; fuel_l: number; objective: number | null;
  truck_empty_km?: number; truck_loaded_km?: number; truck_fuel_l?: number;  // dump trucks, reported separately
}
export interface MetricsComparison {
  baseline: Metrics; optimized: Metrics; deadhead_pct: number; time_pct: number; fuel_pct: number; objective_pct: number;
}
export interface Plan {
  id: string; created_at: number; solver: string; weights: ObjectiveWeights;
  routes: Record<string, RouteStep[]>; vehicle_metrics: Record<string, Metrics>; metrics: Metrics;
  unassigned_task_ids: string[]; plan_start_t: number;
}
export interface SimEvent {
  id: string; t: number; type: EventType; payload: Record<string, unknown>; text: string;
}
export interface VehicleDiff {
  vehicle_id: string; added_task_ids: string[]; removed_task_ids: string[]; delta_km: number;
  old_finish_t: number | null; new_finish_t: number | null; delta_finish_min: number;
}
export interface ReplanProposal {
  id: string; status: ProposalStatus; reason_event_id: string; reason_text: string; created_at: number;
  diffs: VehicleDiff[]; metrics_before: Metrics; metrics_after: Metrics; new_plan: Plan;
  auto_apply_at: number | null; auto_apply_in_s: number | null;
}
export interface TaskSummary {
  total: number; pending: number; assigned: number; in_progress: number; done: number; cancelled: number;
  done_km: number; total_km: number;
}
export interface SimState {
  t: number; clock: string; speed: 1 | 10 | 60 | number; running: boolean; vehicles: Vehicle[];
  task_statuses: Record<string, TaskStatus>; tasks_summary: TaskSummary; metrics: Metrics;
  plan_metrics: Metrics; baseline_metrics: Metrics | null; comparison: MetricsComparison | null;
  plan_id: string | null; pending_proposal: ReplanProposal | null; recent_events: SimEvent[];
}

// ---- API payloads
export interface AppConfig {
  demo_auto_apply: boolean; auto_apply_delay_s: number; weights: ObjectiveWeights;
  solver_time_limit_s: number; sim_speed: number; snapshot_interval_ms: number; n_plows: number; n_trucks: number;
}
export interface SimControlRequest { action: "start" | "pause" | "reset"; speed?: number | null }
export interface BreakdownRequest { vehicle_id: string }
export interface NewRequestBody {
  edge_id?: string | null; lon?: number | null; lat?: number | null; street_name?: string | null;
  priority?: number; passes?: number;
}
export type ProblemKind = "breakdown" | "blocked" | "stuck" | "already_clean" | "other";
export interface ProblemRequest { vehicle_id: string; kind: ProblemKind; comment?: string | null }
export interface SolveRequest { weights?: ObjectiveWeights | null; time_limit_s?: number | null }
export interface CompleteTaskRequest { vehicle_id: string }
export interface DriverView {
  vehicle: Vehicle; current_task: Task | null; next_tasks: Task[];
  route_geojson: GeoJSON.FeatureCollection | null; t: number; clock: string;
}
export interface Basemap {
  bbox: [number, number, number, number];
  layers: { roads: GeoJSON.FeatureCollection; water: GeoJSON.FeatureCollection;
            parks: GeoJSON.FeatureCollection; buildings: GeoJSON.FeatureCollection };
  pois?: { kind: "depot" | "dump"; name: string; lon: number; lat: number }[];
}

// ---- WebSocket
export type ServerMessage =
  | { type: "snapshot"; data: SimState }
  | { type: "event"; data: SimEvent }
  | { type: "proposal"; data: ReplanProposal | null }
  | { type: "plan"; data: Plan }
  | { type: "status"; data: { status: string } }
  | { type: "pong"; data: null };
export type ClientMessage = { type: "ping" };
