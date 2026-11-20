"""Snegoplan shared contracts (pydantic v2).

EVERYTHING that crosses a module boundary (core <-> solver <-> sim <-> api <-> frontend)
is defined here. The TypeScript mirror lives in frontend/src/api/types.ts and MUST be
kept in sync (field names identical, snake_case, enums as string unions).

Conventions
-----------
* Coordinates: WGS84, always [lon, lat] order (GeoJSON order).
* Lengths in metres (`*_m`) unless the name says `_km`. Times: sim seconds (`t`, `t_start`,
  `t_end`, `*_s`) or minutes where the name says `_min`.
* Sim time `t` = float seconds since sim start (t=0 at scenario start).
* Ids are strings. Edge id = "<u>-<v>-<key>" (see core.graph.make_edge_id).
* Human text (Event.text, street names, reasons) is Russian.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(use_enum_values=True, extra="forbid")


# --------------------------------------------------------------------------- enums
class TaskStatus(StrEnum):
    pending = "pending"
    assigned = "assigned"
    in_progress = "in_progress"
    done = "done"
    cancelled = "cancelled"


class VehicleKind(StrEnum):
    plow = "plow"      # id prefix "П-"
    truck = "truck"    # dump truck, id prefix "С-"


class VehicleStatus(StrEnum):
    idle = "idle"
    servicing = "servicing"        # plow: plowing a required edge; truck: loaded haul
    deadheading = "deadheading"    # empty / not plowing, going to next task
    loaded = "loaded"              # truck loaded (going to dump)
    returning = "returning"        # going back to depot
    broken = "broken"
    at_depot = "at_depot"
    finished = "finished"          # plow completed its route at the finish node (snow dump)


class StepKind(StrEnum):
    service = "service"      # plow: plowing a required edge. truck: loaded haul edge (loaded=True)
    deadhead = "deadhead"    # moving without service (empty)
    unload = "unload"        # zero-length step at the dump node (edge_id None), fixed time
    return_ = "return"       # final trip to depot (counts as deadhead in metrics)


class EventType(StrEnum):
    breakdown = "breakdown"
    new_request = "new_request"
    task_done = "task_done"
    plan_applied = "plan_applied"
    proposal_created = "proposal_created"
    proposal_rejected = "proposal_rejected"
    vehicle_at_depot = "vehicle_at_depot"
    sim_control = "sim_control"
    info = "info"


class ProposalStatus(StrEnum):
    pending = "pending"
    applied = "applied"
    rejected = "rejected"
    expired = "expired"


# --------------------------------------------------------------------------- graph
class Node(_Base):
    id: int
    lon: float
    lat: float
    elevation_m: float = 0.0
    kind: Literal["junction", "depot", "dump"] = "junction"
    name: str | None = None            # e.g. "База №1", "Снегоотвал №1"


class Edge(_Base):
    """One DIRECTED edge. A two-way street = two Edge records (u->v, v->u), linked by
    `reverse_edge_id`. `slope_pct` is for travelling u->v (rise/run*100, + = uphill);
    the reverse edge has the negated slope. `geometry` is oriented u->v."""
    id: str                            # "<u>-<v>-<key>"
    u: int
    v: int
    key: int = 0
    length_m: float
    slope_pct: float = 0.0
    geometry: list[list[float]]        # [[lon,lat],...] >= 2 points, first==node u, last==node v
    name: str | None = None
    highway: str = "residential"
    oneway: bool = False
    required: bool = True              # street must be plowed (belongs to a Task)
    street_id: str | None = None       # groups both directions of one physical street segment
    reverse_edge_id: str | None = None
    task_id: str | None = None         # filled by core.graph.build_tasks


class Task(_Base):
    """One undirected street segment to plow. Servicing ANY edge in `edge_ids` counts as a
    pass (two-way street: 2 alternative edges; one-way: 1 edge). `passes` = how many times
    it must be serviced (default 1; heavy snowfall / wide street -> 2)."""
    id: str                            # "T-0001"
    edge_ids: list[str]
    u: int
    v: int
    street_name: str | None = None
    length_m: float
    passes: int = 1
    passes_done: int = 0
    status: TaskStatus = TaskStatus.pending
    assigned_vehicle: str | None = None
    priority: int = 1                  # 1 normal, 2 high (e.g. new_request), 3 urgent
    source: Literal["initial", "request"] = "initial"


# --------------------------------------------------------------------------- vehicles & routes
class Position(_Base):
    lon: float
    lat: float
    edge_id: str | None = None         # None => at a node (e.g. depot)
    offset_m: float = 0.0              # distance from edge start along its geometry
    heading_deg: float | None = None


class RouteStep(_Base):
    """One atomic movement. Routes are EDGE-LEVEL: deadhead paths are expanded into one
    step per directed edge so the simulator can move along geometry. Consecutive steps are
    contiguous (step[i].to_node == step[i+1].from_node)."""
    kind: StepKind
    edge_id: str | None                # None only for kind=unload
    task_id: str | None = None         # set for kind=service (plow)
    from_node: int
    to_node: int
    length_m: float = 0.0
    loaded: bool = False
    t_start: float                     # planned sim time, s
    t_end: float
    fuel_l: float = 0.0
    done: bool = False                 # executed by the sim (history kept for UI)


class Vehicle(_Base):
    id: str                            # "П-01" / "С-01"
    kind: VehicleKind
    name: str = ""                     # display name, Russian
    depot_node: int
    start_node: int | None = None      # where it begins (default depot_node)
    service_speed_kmh: float = 15.0    # plowing speed on flat (trucks: loaded haul speed)
    deadhead_speed_kmh: float = 35.0
    capacity_m3: float = 0.0           # trucks only
    status: VehicleStatus = VehicleStatus.at_depot
    loaded: bool = False
    position: Position | None = None
    route: list[RouteStep] = Field(default_factory=list)
    route_index: int = 0               # index of the step currently executed
    current_task_id: str | None = None
    distance_km: float = 0.0           # travelled so far
    fuel_used_l: float = 0.0
    trail: list[list[float]] = Field(default_factory=list)  # [lon,lat] path traversed since previous broadcast snapshot


# --------------------------------------------------------------------------- metrics / plan
class ObjectiveWeights(_Base):
    deadhead: float = 0.5
    time: float = 0.3
    fuel: float = 0.2


class Metrics(_Base):
    deadhead_km: float = 0.0
    service_km: float = 0.0
    total_time_min: float = 0.0        # makespan: max over vehicles of end - plan start
    fuel_l: float = 0.0
    objective: float | None = None     # weighted score (lower is better), see solver.cost.objective
    # dump trucks are reported separately (NOT included in the plow metrics above); live metrics only
    truck_empty_km: float = 0.0
    truck_loaded_km: float = 0.0
    truck_fuel_l: float = 0.0


class MetricsComparison(_Base):
    """`*_pct` = (optimized - baseline)/baseline*100; NEGATIVE = improvement."""
    baseline: Metrics
    optimized: Metrics
    deadhead_pct: float = 0.0
    time_pct: float = 0.0
    fuel_pct: float = 0.0
    objective_pct: float = 0.0


class Plan(_Base):
    id: str
    created_at: float = 0.0            # sim time of creation
    solver: str = "carp"               # "carp" | "baseline" | "trucks"
    weights: ObjectiveWeights = Field(default_factory=ObjectiveWeights)
    routes: dict[str, list[RouteStep]] = Field(default_factory=dict)   # vehicle_id -> steps
    vehicle_metrics: dict[str, Metrics] = Field(default_factory=dict)
    metrics: Metrics = Field(default_factory=Metrics)                  # whole fleet
    unassigned_task_ids: list[str] = Field(default_factory=list)
    plan_start_t: float = 0.0          # sim time that step times are relative to


# --------------------------------------------------------------------------- events / proposals
class Event(_Base):
    id: str                            # "E-0001"
    t: float
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)  # breakdown:{vehicle_id}; new_request:{task_id,edge_id}; task_done:{task_id,vehicle_id}
    text: str                          # Russian, e.g. "Поломка: П-03 остановился на ул. Ленинской"


class VehicleDiff(_Base):
    vehicle_id: str
    added_task_ids: list[str] = Field(default_factory=list)
    removed_task_ids: list[str] = Field(default_factory=list)
    delta_km: float = 0.0              # new total route km - old (remaining part)
    old_finish_t: float | None = None
    new_finish_t: float | None = None
    delta_finish_min: float = 0.0


class ReplanProposal(_Base):
    id: str                            # "R-0001"
    status: ProposalStatus = ProposalStatus.pending
    reason_event_id: str
    reason_text: str                   # Russian
    created_at: float                  # sim time
    diffs: list[VehicleDiff] = Field(default_factory=list)
    metrics_before: Metrics            # remaining-work metrics of current plan
    metrics_after: Metrics             # remaining-work metrics of proposed plan
    new_plan: Plan
    auto_apply_at: float | None = None  # WALL-clock unix seconds (time.time()); None = manual only
    auto_apply_in_s: float | None = None  # convenience countdown, filled when serialised


# --------------------------------------------------------------------------- sim state
class TaskSummary(_Base):
    total: int = 0
    pending: int = 0
    assigned: int = 0
    in_progress: int = 0
    done: int = 0
    cancelled: int = 0
    done_km: float = 0.0
    total_km: float = 0.0


class SimState(_Base):
    t: float                           # sim seconds
    clock: str = ""                    # "HH:MM" of sim wall clock (scenario start 06:00)
    speed: int = 1                     # 1 | 10 | 60
    running: bool = False
    vehicles: list[Vehicle]            # route trimmed to remaining steps + a few done ones
    task_statuses: dict[str, TaskStatus] = Field(default_factory=dict)  # task_id -> status
    tasks_summary: TaskSummary = Field(default_factory=TaskSummary)
    metrics: Metrics = Field(default_factory=Metrics)             # live (actual so far)
    plan_metrics: Metrics = Field(default_factory=Metrics)        # planned (current plan)
    baseline_metrics: Metrics | None = None
    comparison: MetricsComparison | None = None
    plan_id: str | None = None
    pending_proposal: ReplanProposal | None = None
    recent_events: list[Event] = Field(default_factory=list)      # last 20, newest last


# --------------------------------------------------------------------------- api payloads
class AppConfig(_Base):
    demo_auto_apply: bool = True
    auto_apply_delay_s: float = 8.0
    weights: ObjectiveWeights = Field(default_factory=ObjectiveWeights)
    solver_time_limit_s: float = 5.0
    sim_speed: int = 1
    snapshot_interval_ms: int = 250
    n_plows: int = 4
    n_trucks: int = 0


class SimControlRequest(_Base):
    action: Literal["start", "pause", "reset"]
    speed: int | None = None


class BreakdownRequest(_Base):
    vehicle_id: str


class NewRequestBody(_Base):
    """Provide edge_id OR (lon,lat) OR street_name; priority default 2."""
    edge_id: str | None = None
    lon: float | None = None
    lat: float | None = None
    street_name: str | None = None
    priority: int = 2
    passes: int = 1


class SolveRequest(_Base):
    weights: ObjectiveWeights | None = None
    time_limit_s: float | None = None


class CompleteTaskRequest(_Base):
    vehicle_id: str


class ProblemRequest(_Base):
    """Driver problem report. kind: breakdown | blocked | stuck | already_clean | other."""
    vehicle_id: str
    kind: Literal["breakdown", "blocked", "stuck", "already_clean", "other"]
    comment: str | None = None


class DriverView(_Base):
    vehicle: Vehicle
    current_task: Task | None = None
    next_tasks: list[Task] = Field(default_factory=list)      # next <=5 service tasks
    route_geojson: dict[str, Any] | None = None               # FeatureCollection of remaining steps
    t: float = 0.0
    clock: str = ""


def make_default_fleet(depot_node: int, n_plows: int = 4, n_trucks: int = 0) -> list[Vehicle]:
    """Default demo fleet. Ids: П-01.., С-01.."""
    fleet: list[Vehicle] = []
    for i in range(1, n_plows + 1):
        fleet.append(Vehicle(id=f"П-{i:02d}", kind=VehicleKind.plow, name=f"Снегоуборщик П-{i:02d}",
                             depot_node=depot_node, start_node=depot_node))
    for i in range(1, n_trucks + 1):
        fleet.append(Vehicle(id=f"С-{i:02d}", kind=VehicleKind.truck, name=f"Самосвал С-{i:02d}",
                             depot_node=depot_node, start_node=depot_node,
                             service_speed_kmh=25.0, deadhead_speed_kmh=40.0, capacity_m3=10.0))
    return fleet
