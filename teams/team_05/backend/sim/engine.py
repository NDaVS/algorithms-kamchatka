"""Simulation engine. OWNER: sim agent. See CONTRACTS.md section 4.

Threading model
---------------
All public methods take an internal RLock, so the engine may be touched from the asyncio loop thread
(tick / snapshot) and from worker threads (REST handlers, executor replans) at the same time.
The *solver call itself runs outside the lock* (on copies), so ticks never freeze during a replan.

Two replan modes:
  * sync  (default; tests, scripts): inject_* -> replan() immediately, proposal ready on return.
  * async (`engine.async_replan = True`, used by the app): inject_* only logs the event and queues a
    replan; the app runs `engine.run_pending_replans()` in a thread executor.

Execution model
---------------
Each vehicle executes `vehicle.route` (edge-level steps, ABSOLUTE sim times) starting at
`route_index`, `rt.offset` metres into the current step. Speed/fuel come from solver.cost, i.e. the
same model the solver plans with. A newly applied plan is *re-based* onto the vehicle's present
state (`_compose`): the step currently being driven is carried over, gaps are bridged with shortest
deadhead paths, times are recomputed. Service steps whose task has meanwhile been completed degrade
to deadhead when entered.
"""
from __future__ import annotations

import copy
import logging
import math
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import networkx as nx

from core import graph as cg
from core.models import (AppConfig, Event, EventType, Metrics, MetricsComparison, ObjectiveWeights, Plan,
                         Position, ProposalStatus, ReplanProposal, RouteStep, SimState, StepKind, Task,
                         TaskStatus, TaskSummary, Vehicle, VehicleDiff, VehicleKind, VehicleStatus, DriverView)
from solver.cost import finish_node, objective, unload_cost, vehicle_edge_cost
from solver.trucks import Trip, plan_truck_trips, select_pickup_tasks
from solver.metrics import compare_metrics, compute_metrics, fill_plan_metrics, metrics_from_steps
from sim import events as ev_txt
from sim.events import EventLog

log = logging.getLogger("snegoplan.sim")

SolveFn = Callable[..., Plan]  # solve_plows signature

MAX_SUBSTEP_S = 5.0
_SVC, _DEAD, _RET, _UNL = (StepKind.service.value, StepKind.deadhead.value, StepKind.return_.value,
                           StepKind.unload.value)


TRAIL_MAX = 500


class SimNotFound(KeyError):
    """Unknown vehicle / task / proposal id (REST 404)."""


class SimConflict(ValueError):
    """Operation not valid in the current state (REST 409)."""


@dataclass
class _Rt:
    """Per-vehicle runtime state (not part of the public model)."""
    node: int                    # node the vehicle is at when not inside a step
    offset: float = 0.0          # metres driven in the current step
    entered: bool = False        # current step started
    length: float = 0.0
    speed_mps: float = 1.0
    fuel_per_m: float = 0.0
    mode: str = "deadhead"       # service | deadhead | unload
    unload_left: float = 0.0
    trail: list = field(default_factory=list)   # [lon,lat] points traversed since last broadcast
    moved: bool = False          # moved since the route was installed (for 'back at depot' event)


@dataclass
class _Job:
    """Inputs of one replan, captured under the lock; solved outside it."""
    event: Event | None
    t: float
    tasks: list[Task]
    vehicles: list[Vehicle]
    start_positions: dict[str, Position]
    weights: ObjectiveWeights
    time_limit_s: float
    old_remaining: dict[str, list[RouteStep]]
    old_tasks: dict[str, list[str]]
    before: Metrics



def _cut_from(coords: list[list[float]], frac: float) -> list[list[float]]:
    """Polyline remainder from fraction `frac` (by length) to the end; first point = interpolated position."""
    if frac <= 0 or len(coords) < 2:
        return [list(c) for c in coords]
    lon, lat, _ = cg._interp(coords, frac)
    seg = [cg.haversine_m(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1]) for i in range(len(coords) - 1)]
    target = frac * sum(seg)
    acc = 0.0
    out = [[lon, lat]]
    for i, sl in enumerate(seg):
        if acc + sl > target + 1e-9:
            out.extend([list(c) for c in coords[i + 1:]])
            break
        acc += sl
    if len(out) < 2:
        out.append(list(coords[-1]))
    return out


class SimEngine:
    SCENARIO_START_CLOCK = "06:00"
    SPEEDS = (1, 10, 60)

    def __init__(self, graph: nx.MultiDiGraph, tasks: list[Task], vehicles: list[Vehicle],
                 config: AppConfig, solve_fn: SolveFn, baseline_fn: SolveFn, *,
                 clock: Callable[[], float] = time.time, seed: int = 7) -> None:
        """Builds initial plan (solve_fn) and baseline (baseline_fn) at t=0; vehicles at depot.
        Starts paused. `clock` = wall clock (injectable for tests)."""
        self.graph = graph
        self.config = config
        self.solve_fn = solve_fn
        self.baseline_fn = baseline_fn
        self.clock = clock
        self.async_replan = False
        self._lock = threading.RLock()
        self._rng = random.Random(seed)
        self.events = EventLog()
        self.depot_node: int = graph.graph.get("depot_node", vehicles[0].depot_node if vehicles else 0)
        self._tasks0 = [t.model_copy(deep=True) for t in tasks]
        self._vehicles0 = [v.model_copy(deep=True) for v in vehicles]
        self._edge_task0: dict[str, str | None] = {}   # original edge task_id for edges touched by requests
        self.speed = config.sim_speed if config.sim_speed in self.SPEEDS else 1
        self.baseline: Plan | None = None
        self._plan0: Plan | None = None
        self._init_state()
        self.plan: Plan = self._solve_initial()
        self._plan0 = self.plan.model_copy(deep=True)
        self._install_plan(self.plan)

    # ------------------------------------------------------------------ setup / reset
    def _init_state(self) -> None:
        self.t = 0.0
        self.running = False
        self.tasks: list[Task] = [t.model_copy(deep=True) for t in self._tasks0]
        self.task_by_id: dict[str, Task] = {t.id: t for t in self.tasks}
        self.vehicles: dict[str, Vehicle] = {}
        self.rt: dict[str, _Rt] = {}
        for v0 in self._vehicles0:
            v = v0.model_copy(deep=True)
            node = v.start_node if v.start_node is not None else v.depot_node
            v.route, v.route_index, v.distance_km, v.fuel_used_l = [], 0, 0.0, 0.0
            v.loaded, v.current_task_id = False, None
            v.status = VehicleStatus.at_depot if node == v.depot_node else VehicleStatus.idle
            v.position = self._node_pos(node)
            self.vehicles[v.id] = v
            self.rt[v.id] = _Rt(node=node)
        self.events.clear()
        self.proposals: dict[str, ReplanProposal] = {}
        self._pending_id: str | None = None
        self._replan_queue: list[Event] = []
        self._proposal_seq = 0
        self._plan_seq = 0
        self.plan_version = getattr(self, "plan_version", 0) + 1
        self.proposal_version = getattr(self, "proposal_version", 0) + 1
        self._tot_svc_m = self._tot_dead_m = self._tot_fuel = 0.0
        self._truck_empty_m = self._truck_loaded_m = self._truck_fuel = 0.0
        self._truck_queue: dict[str, list[Trip]] = {}
        self._truck_served: set[str] = set()      # task ids whose truck trip was already dispatched
        self._last_active_t = 0.0
        # restore edge->task links touched by request tasks
        for eid, tid in self._edge_task0.items():
            cg.edge_data(self.graph, eid)["task_id"] = tid
        self._edge_task0.clear()
        self._street_index: dict[str, list[str]] | None = None

    def _solve_initial(self) -> Plan:
        plan = self.solve_fn(self.graph, [t.model_copy(deep=True) for t in self.tasks],
                             [v.model_copy(deep=True) for v in self.vehicles.values()],
                             weights=self.config.weights, start_positions=None,
                             time_limit_s=self.config.solver_time_limit_s)
        try:
            base = self.baseline_fn(self.graph, [t.model_copy(deep=True) for t in self.tasks],
                                    [v.model_copy(deep=True) for v in self.vehicles.values()],
                                    weights=self.config.weights, start_positions=None)
            fill_plan_metrics(base)
            base.id = "P-0000"
            self.baseline = base
        except Exception:  # baseline is only a reference; never block the sim on it
            log.exception("baseline plan failed; comparison disabled")
            self.baseline = None
        plan.id = self._next_plan_id()
        fill_plan_metrics(plan, self.baseline.metrics if self.baseline else None)
        return plan

    def reset(self) -> None:
        """Back to t=0 with initial plan; clears events/proposal."""
        with self._lock:
            self._init_state()
            self.plan = self._plan0.model_copy(deep=True)
            self._plan_seq = 1
            self._install_plan(self.plan)
            self.events.add(0.0, EventType.sim_control, {"action": "reset"}, "Симуляция сброшена")

    def set_config(self, cfg: AppConfig) -> None:
        """Hot-apply config (weights, auto-apply, time limit, speed). n_plows/n_trucks need a restart."""
        with self._lock:
            if cfg.sim_speed not in self.SPEEDS:
                raise ValueError(f"speed must be one of {self.SPEEDS}")
            self.config = cfg
            self.speed = cfg.sim_speed

    # ------------------------------------------------------------------ small helpers
    def _next_plan_id(self) -> str:
        self._plan_seq += 1
        return f"P-{self._plan_seq:04d}"

    def _node_pos(self, node: int) -> Position:
        d = self.graph.nodes[node]
        return Position(lon=d["x"], lat=d["y"])

    def _edge_street(self, edge_id: str | None) -> str | None:
        return cg.edge_data(self.graph, edge_id).get("name") if edge_id else None

    def _heading(self, edge_id: str, offset: float) -> float | None:
        d = cg.edge_data(self.graph, edge_id)
        g = d.get("geometry") or []
        if len(g) < 2:
            return None
        segs = [cg.haversine_m(g[i][0], g[i][1], g[i + 1][0], g[i + 1][1]) for i in range(len(g) - 1)]
        tot = sum(segs) or 1.0
        target = max(0.0, min(offset, d["length"])) / (d["length"] or 1.0) * tot
        acc = 0.0
        for i, s in enumerate(segs):
            if acc + s >= target or i == len(segs) - 1:
                (x1, y1), (x2, y2) = g[i], g[i + 1]
                dx = (x2 - x1) * math.cos(math.radians((y1 + y2) / 2))
                return (math.degrees(math.atan2(dx, y2 - y1)) + 360.0) % 360.0
            acc += s
        return None

    def _vehicle_pos(self, v: Vehicle, rt: _Rt) -> Position:
        if rt.entered and v.route_index < len(v.route):
            step = v.route[v.route_index]
            if step.edge_id:
                p = cg.edge_position(self.graph, step.edge_id, rt.offset)
                return p.model_copy(update={"edge_id": step.edge_id, "offset_m": round(rt.offset, 1),
                                            "heading_deg": p.heading_deg if p.heading_deg is not None
                                            else self._heading(step.edge_id, rt.offset)})
        return self._node_pos(rt.node)

    def _trail_add(self, rt: _Rt, edge_id: str, o0: float, o1: float) -> None:
        """Append the edge polyline between offsets o0..o1 (same length-proportional rule as edge_position)."""
        d = cg.edge_data(self.graph, edge_id)
        g = d.get("geometry") or []
        if len(g) < 2:
            return
        length = d.get("length") or 1.0
        segs = [cg.haversine_m(g[i][0], g[i][1], g[i + 1][0], g[i + 1][1]) for i in range(len(g) - 1)]
        tot = sum(segs) or 1.0
        a = max(0.0, min(1.0, o0 / length)) * tot
        b = max(0.0, min(1.0, o1 / length)) * tot

        def pt(i: int, dd: float) -> list[float]:
            t = (dd - acc) / segs[i] if segs[i] > 0 else 0.0
            return [g[i][0] + (g[i + 1][0] - g[i][0]) * t, g[i][1] + (g[i + 1][1] - g[i][1]) * t]
        pts: list[list[float]] = []
        acc = 0.0
        started = False
        for i, s in enumerate(segs):
            if not started and acc + s >= a:
                pts.append(pt(i, a))
                started = True
            if started:
                if acc + s >= b:
                    pts.append(pt(i, b))
                    break
                pts.append([g[i + 1][0], g[i + 1][1]])
            acc += s
        tr = rt.trail
        for p in pts:
            if not tr or abs(tr[-1][0] - p[0]) > 1e-9 or abs(tr[-1][1] - p[1]) > 1e-9:
                tr.append([round(p[0], 6), round(p[1], 6)])
        if len(tr) > TRAIL_MAX:
            del tr[:len(tr) - TRAIL_MAX]

    def clear_trails(self) -> None:
        with self._lock:
            for rt in self.rt.values():
                rt.trail.clear()

    def _carry_step(self, v: Vehicle, rt: _Rt) -> RouteStep | None:
        if rt.entered and v.route_index < len(v.route):
            return v.route[v.route_index]
        return None

    def _is_plow(self, v: Vehicle) -> bool:
        return v.kind == VehicleKind.plow.value

    # ------------------------------------------------------------------ clock
    def tick(self, dt: float) -> None:
        """Advance by `dt` REAL seconds: sim_dt = dt * speed (if running). Sub-steps <= 5 sim-s."""
        with self._lock:
            if self.running and dt > 0:
                sim_dt = dt * self.speed
                n = max(1, math.ceil(sim_dt / MAX_SUBSTEP_S))
                sub = sim_dt / n
                for _ in range(n):
                    self._substep(sub)
                for v in self.vehicles.values():
                    if v.status != VehicleStatus.broken.value:
                        v.position = self._vehicle_pos(v, self.rt[v.id])
            p = self._pending_id and self.proposals.get(self._pending_id)
            if p and p.status == ProposalStatus.pending.value and p.auto_apply_at is not None \
                    and self.clock() >= p.auto_apply_at:
                self.apply_proposal(p.id)

    def set_speed(self, speed: int) -> None:
        if speed not in self.SPEEDS:
            raise ValueError(f"speed must be one of {self.SPEEDS}")
        with self._lock:
            self.speed = speed
            self.config = self.config.model_copy(update={"sim_speed": speed})
            self.events.add(self.t, EventType.sim_control, {"action": "speed", "speed": speed},
                            f"Скорость симуляции: ×{speed}")

    def start(self) -> None:
        with self._lock:
            if not self.running:
                self.running = True
                self.events.add(self.t, EventType.sim_control, {"action": "start"}, "Симуляция запущена")

    def pause(self) -> None:
        with self._lock:
            if self.running:
                self.running = False
                self.events.add(self.t, EventType.sim_control, {"action": "pause"}, "Симуляция приостановлена")

    # ------------------------------------------------------------------ movement
    def _substep(self, dt: float) -> None:
        t0 = self.t
        moved_any = False
        for v in self.vehicles.values():
            if v.status == VehicleStatus.broken.value:
                continue
            rt = self.rt[v.id]
            if not self._is_plow(v):
                self._dispatch_truck(v, rt)
            if v.route_index >= len(v.route):
                self._refresh_status(v, rt)
                continue
            m = self._advance(v, rt, dt, t0)
            if self._is_plow(v):
                moved_any |= m
            self._refresh_status(v, rt)
        self.t = t0 + dt
        if moved_any:
            self._last_active_t = self.t

    def _advance(self, v: Vehicle, rt: _Rt, dt: float, t0: float) -> bool:
        remaining = dt
        moved = False
        while remaining > 1e-9 and v.route_index < len(v.route):
            step = v.route[v.route_index]
            if not rt.entered:
                self._enter_step(v, rt, step)
            if rt.mode == "unload":
                use = min(rt.unload_left, remaining)
                rt.unload_left -= use
                remaining -= use
                moved = rt.moved = True
                if rt.unload_left <= 1e-9:
                    self._complete_step(v, rt, step, t0 + dt - remaining)
                continue
            dist_left = max(0.0, rt.length - rt.offset)
            t_need = dist_left / rt.speed_mps if rt.speed_mps > 0 else 0.0
            if t_need <= remaining + 1e-9:
                dist, remaining, done = dist_left, remaining - t_need, True
            else:
                dist, remaining, done = rt.speed_mps * remaining, 0.0, False
            if dist > 0:
                moved = rt.moved = True
                o_old = rt.offset
                rt.offset += dist
                if step.edge_id:
                    self._trail_add(rt, step.edge_id, o_old, rt.offset)
                fuel = rt.fuel_per_m * dist
                v.distance_km += dist / 1000.0
                v.fuel_used_l += fuel
                if not self._is_plow(v):      # trucks are accounted separately from plow metrics
                    self._truck_fuel += fuel
                    if step.loaded:
                        self._truck_loaded_m += dist
                    else:
                        self._truck_empty_m += dist
                else:
                    self._tot_fuel += fuel
                    if rt.mode == "service":
                        self._tot_svc_m += dist
                    else:
                        self._tot_dead_m += dist
            if done:
                self._complete_step(v, rt, step, t0 + dt - remaining)
        return moved

    def _servicing_count(self, task_id: str, exclude: str) -> int:
        n = 0
        for o in self.vehicles.values():
            if o.id == exclude or not self.rt[o.id].entered or o.route_index >= len(o.route):
                continue
            s = o.route[o.route_index]
            if s.kind == _SVC and s.task_id == task_id:
                n += 1
        return n

    def _enter_step(self, v: Vehicle, rt: _Rt, step: RouteStep) -> None:
        rt.entered = True
        rt.offset = 0.0
        if step.kind == _UNL or step.edge_id is None:
            rt.mode, rt.length, rt.unload_left = "unload", 0.0, unload_cost().time_s
            v.current_task_id = None
            return
        truck = not self._is_plow(v)
        if step.kind == _SVC and truck:
            step.task_id = None            # loaded haul: not a plowing pass
        elif step.kind == _SVC:
            task = self.task_by_id.get(step.task_id or "")
            if (task is None or task.status in (TaskStatus.done.value, TaskStatus.cancelled.value)
                    or task.passes_done + self._servicing_count(task.id, v.id) >= task.passes):
                step.kind, step.task_id = StepKind.deadhead.value, None   # already served: just drive through
            else:
                task.status = TaskStatus.in_progress.value
                task.assigned_vehicle = v.id
                v.current_task_id = task.id
        if step.kind != _SVC:
            v.current_task_id = None
        mode = "service" if (step.kind == _SVC and not truck) else "deadhead"
        d = cg.edge_data(self.graph, step.edge_id)
        length = float(d["length"])
        c = vehicle_edge_cost(v, length, d.get("slope_pct", 0.0), mode, step.loaded)
        rt.mode, rt.length = mode, length
        rt.speed_mps = max(c.speed_kmh / 3.6, 0.1)
        rt.fuel_per_m = c.fuel_l / length if length > 0 else 0.0
        v.loaded = step.loaded

    def _complete_step(self, v: Vehicle, rt: _Rt, step: RouteStep, t_ev: float) -> None:
        step.done = True
        if step.kind == _UNL and not self._is_plow(v):
            self.events.add(t_ev, EventType.info, {"vehicle_id": v.id}, f"{v.id} выгрузил снег на снегоотвале")
        if step.kind == _SVC and step.task_id:
            task = self.task_by_id.get(step.task_id)
            if task and task.status not in (TaskStatus.done.value, TaskStatus.cancelled.value) \
                    and task.passes_done < task.passes:
                task.passes_done += 1
                if task.passes_done >= task.passes:
                    task.status = TaskStatus.done.value
                    self.events.add(t_ev, EventType.task_done, {"task_id": task.id, "vehicle_id": v.id},
                                    ev_txt.task_done_text(v.id, task.id))
                else:
                    task.status = TaskStatus.assigned.value
        v.current_task_id = None
        rt.node = step.to_node
        rt.offset, rt.entered = 0.0, False
        v.route_index += 1
        if v.route_index >= len(v.route) and rt.moved:
            rt.moved = False
            fin = finish_node(self.graph, v)
            if rt.node == fin and self._is_plow(v):
                at_dump = fin != v.depot_node
                self.events.add(t_ev, EventType.vehicle_at_depot, {"vehicle_id": v.id},
                                ev_txt.at_dump_finish_text(v.id) if at_dump else ev_txt.at_depot_text(v.id))

    def _refresh_status(self, v: Vehicle, rt: _Rt) -> None:
        if v.status == VehicleStatus.broken.value:
            return
        if rt.entered and v.route_index < len(v.route):
            s = v.route[v.route_index]
            if s.kind == _SVC and self._is_plow(v):
                v.status = VehicleStatus.servicing
            elif s.kind == _RET:
                v.status = VehicleStatus.returning
            elif s.kind == _UNL:
                v.status = VehicleStatus.servicing      # trucks: UI shows "Выгрузка"
            else:
                v.status = VehicleStatus.loaded if (s.loaded and v.kind == VehicleKind.truck.value) \
                    else VehicleStatus.deadheading
        else:
            if rt.node == v.depot_node:
                v.status = VehicleStatus.at_depot
            elif (self._is_plow(v) and v.route and v.route_index >= len(v.route)
                  and rt.node == finish_node(self.graph, v)):
                v.status = VehicleStatus.finished
            else:
                v.status = VehicleStatus.idle

    # ------------------------------------------------------------------ route composition
    def _mk_step(self, v: Vehicle, kind: str, edge_id: str | None, task_id: str | None, frm: int, to: int,
                 loaded: bool, t_start: float) -> RouteStep:
        if edge_id is None:
            c, length = unload_cost(), 0.0
        else:
            d = cg.edge_data(self.graph, edge_id)
            length = float(d["length"])
            c = vehicle_edge_cost(v, length, d.get("slope_pct", 0.0),
                                  "service" if kind == _SVC else "deadhead", loaded)
        return RouteStep(kind=kind, edge_id=edge_id, task_id=task_id, from_node=frm, to_node=to,
                         length_m=length, loaded=loaded, t_start=t_start, t_end=t_start + c.time_s,
                         fuel_l=c.fuel_l)

    def _bridge(self, v: Vehicle, src: int, dst: int, kind: str, t: float) -> list[RouteStep]:
        if src == dst:
            return []
        try:
            path = cg.shortest_path_edges(self.graph, src, dst, kind=v.kind, mode="deadhead", loaded=False)
        except nx.NetworkXNoPath:
            log.warning("no bridge path %s -> %s for %s", src, dst, v.id)
            return []
        out = []
        for eid in path:
            u, w, _ = cg.parse_edge_id(eid)
            s = self._mk_step(v, kind, eid, None, u, w, False, t)
            t = s.t_end
            out.append(s)
        return out

    def _compose(self, v: Vehicle, rt: _Rt, new_steps: list[RouteStep]) -> tuple[list[RouteStep], int]:
        """Re-base planned steps onto the vehicle's present state. Returns (full route, route_index)."""
        idx = v.route_index
        prefix = v.route[:idx]
        carry = self._carry_step(v, rt)
        t = self.t
        steps = list(new_steps)
        if carry is not None:
            t += (rt.unload_left if rt.mode == "unload"
                  else max(0.0, rt.length - rt.offset) / max(rt.speed_mps, 0.1))
            node = carry.to_node
            if steps and carry.edge_id is not None and steps[0].edge_id == carry.edge_id:
                steps.pop(0)       # solver included the remainder of the edge being driven
        else:
            node = rt.node
        out: list[RouteStep] = []
        for s in steps:
            for b in self._bridge(v, node, s.from_node, _DEAD, t):
                out.append(b)
                t = b.t_end
            kind = s.kind
            if kind == _SVC and not s.task_id:
                kind = _DEAD
            ns = self._mk_step(v, kind, s.edge_id, s.task_id if kind == _SVC else None,
                               s.from_node, s.to_node, s.loaded, t)
            out.append(ns)
            t, node = ns.t_end, s.to_node
        if self._is_plow(v) and node != finish_node(self.graph, v):
            for b in self._bridge(v, node, finish_node(self.graph, v), _RET, t):
                out.append(b)
        full = prefix + ([carry] if carry is not None else []) + out
        return full, len(prefix)

    def _install_plan(self, plan: Plan) -> None:
        """Replace routes of all non-broken vehicles from plan.routes (re-based on current state)."""
        for v in self.vehicles.values():
            rt = self.rt[v.id]
            if v.status == VehicleStatus.broken.value:
                v.route, v.route_index = v.route[:v.route_index], v.route_index   # drop unfinished plan
                rt.entered = False
                continue
            if not self._is_plow(v) and v.id not in plan.routes:
                continue
            route, idx = self._compose(v, rt, plan.routes.get(v.id, []))
            v.route, v.route_index = route, idx
            rt.moved = False if not rt.entered else rt.moved
        self._refresh_assignments()
        self._sync_plan_routes(plan)
        self._plan_trucks()

    # ------------------------------------------------------------------ dump trucks
    def _trucks(self) -> list[Vehicle]:
        return [v for v in self.vehicles.values() if not self._is_plow(v) and v.status != VehicleStatus.broken.value]

    def _plan_trucks(self) -> None:
        """(Re)build truck trip queues from the current plow routes: pickups = plowed tasks on major
        streets, ordered by plow service time. Already dispatched trips are kept (route in progress)."""
        trucks = self._trucks()
        dump = self.graph.graph.get("dump_node")
        self._truck_queue = {v.id: [] for v in trucks}
        if not trucks or dump is None:
            return
        try:
            t_start = time.perf_counter()
            ready: dict[str, float] = {}
            for pv in self.vehicles.values():
                if not self._is_plow(pv) or pv.status == VehicleStatus.broken.value:
                    continue
                for s in pv.route[pv.route_index:]:
                    if s.kind == _SVC and s.task_id and not s.done:
                        ready[s.task_id] = max(ready.get(s.task_id, 0.0), s.t_end)
            picks = []
            for task in select_pickup_tasks(self.graph, self.tasks):
                if task.id in self._truck_served:
                    continue
                if task.status == TaskStatus.done.value:
                    picks.append((task.edge_ids[0], 0.0, task.id))
                elif task.id in ready:
                    picks.append((task.edge_ids[0], ready[task.id], task.id))
            start_nodes, free_at = {}, {}
            for v in trucks:
                rt = self.rt[v.id]
                rem = v.route[v.route_index:]
                start_nodes[v.id] = rem[-1].to_node if rem else rt.node
                free_at[v.id] = max(self.t, rem[-1].t_end) if rem else self.t
            self._truck_queue = plan_truck_trips(self.graph, picks, trucks, dump, start_nodes, free_at)
            log.info("truck plan: %d pickups -> %s (%.0f ms)", len(picks),
                     {k: len(q) for k, q in self._truck_queue.items()}, (time.perf_counter() - t_start) * 1000)
        except Exception:
            log.exception("truck planning failed; trucks stay idle")
            self._truck_queue = {v.id: [] for v in trucks}

    def _dispatch_truck(self, v: Vehicle, rt: _Rt) -> None:
        """Idle truck takes its next trip once the plow has served the pickup task (dependency),
        or returns to the depot when its queue is empty."""
        if v.status == VehicleStatus.broken.value or v.route_index < len(v.route) or rt.entered:
            return
        q = self._truck_queue.get(v.id)
        while q:
            trip = q[0]
            task = self.task_by_id.get(trip.ref or "")
            if task is not None and task.status == TaskStatus.cancelled.value:
                q.pop(0)
                continue
            if task is not None and task.status != TaskStatus.done.value:
                return                       # plow has not finished this street yet: wait
            q.pop(0)
            t, node, new = self.t, rt.node, []
            for s in trip.steps:
                for b in self._bridge(v, node, s.from_node, _DEAD, t):
                    new.append(b)
                    t = b.t_end
                ns = self._mk_step(v, s.kind, s.edge_id, None, s.from_node, s.to_node, s.loaded, t)
                new.append(ns)
                t, node = ns.t_end, s.to_node
            v.route = v.route + new
            rt.moved = True
            if task is not None:
                self._truck_served.add(task.id)
            log.info("%s dispatched to %s (%d steps)", v.id, trip.ref, len(new))
            return
        fin = finish_node(self.graph, v)
        if rt.node != fin:
            back = self._bridge(v, rt.node, fin, _RET, self.t)
            if back:
                v.route = v.route + back

    def _sync_plan_routes(self, plan: Plan) -> None:
        """plan.routes := remaining steps (shared objects) per vehicle; metrics refreshed."""
        plan.routes = {vid: v.route[v.route_index:] for vid, v in self.vehicles.items()
                       if self._is_plow(v) and v.status != VehicleStatus.broken.value
                       and (v.route or vid in plan.routes)}
        plan.plan_start_t = self.t
        fill_plan_metrics(plan, self.baseline.metrics if self.baseline else None)

    def _refresh_assignments(self) -> None:
        assigned: dict[str, str] = {}
        for v in self.vehicles.values():
            if v.status == VehicleStatus.broken.value:
                continue
            for s in v.route[v.route_index:]:
                if s.kind == _SVC and s.task_id and not s.done:
                    assigned.setdefault(s.task_id, v.id)
        for t in self.tasks:
            if t.status in (TaskStatus.pending.value, TaskStatus.assigned.value):
                if t.id in assigned:
                    t.status, t.assigned_vehicle = TaskStatus.assigned.value, assigned[t.id]
                else:
                    t.status, t.assigned_vehicle = TaskStatus.pending.value, None

    # ------------------------------------------------------------------ events
    def _expire_pending(self) -> None:
        p = self.proposals.get(self._pending_id) if self._pending_id else None
        if p and p.status == ProposalStatus.pending.value:
            p.status = ProposalStatus.expired.value
            self.proposal_version += 1
        self._pending_id = None

    def _after_event(self, ev: Event) -> None:
        self._expire_pending()
        if self.async_replan:
            self._replan_queue.append(ev)
        else:
            self.replan(ev)

    def inject_breakdown(self, vehicle_id: str) -> Event:
        """Vehicle status=broken, stops in place; its unfinished service tasks return to pending;
        replan with remaining plows from CURRENT positions."""
        with self._lock:
            v = self.vehicles.get(vehicle_id)
            if v is None:
                raise SimNotFound(f"Машина {vehicle_id} не найдена")
            if v.status == VehicleStatus.broken.value:
                raise SimConflict(f"{vehicle_id} уже сломан")
            rt = self.rt[vehicle_id]
            pos = self._vehicle_pos(v, rt)
            street = self._edge_street(pos.edge_id)
            for t in self.tasks:
                if t.assigned_vehicle == vehicle_id and t.status in (TaskStatus.assigned.value,
                                                                     TaskStatus.in_progress.value):
                    t.status, t.assigned_vehicle = TaskStatus.pending.value, None
            v.position, v.status, v.current_task_id, v.loaded = pos, VehicleStatus.broken, None, False
            self._refresh_assignments()
            ev = self.events.add(self.t, EventType.breakdown, {"vehicle_id": vehicle_id,
                                                               "edge_id": pos.edge_id},
                                 ev_txt.breakdown_text(vehicle_id, street))
            self._after_event(ev)
            return ev

    # --- new requests
    def _street_candidates(self, name: str) -> list[str]:
        if self._street_index is None:
            idx: dict[str, list[str]] = {}
            for t in self._tasks0:
                if t.street_name:
                    idx.setdefault(t.street_name.lower(), []).append(t.id)
            self._street_index = idx
        q = name.strip().lower()
        exact = self._street_index.get(q)
        if exact:
            return list(exact)
        return [tid for k, ids in self._street_index.items() if q in k for tid in ids]

    def _new_task_id(self) -> str:
        n = max((int(t.id.split("-")[1]) for t in self.tasks), default=0)
        return f"T-{n + 1:04d}"

    def inject_request(self, edge_id: str | None = None, lon: float | None = None,
                       lat: float | None = None, street_name: str | None = None,
                       priority: int = 2, passes: int = 1) -> Event:
        """Create (or re-open: status pending, passes+=) a Task for the edge/point/street, log
        new_request, replan -> proposal. With no target: random street. A street name matching several
        segments picks one at random (deterministic RNG)."""
        with self._lock:
            passes = max(1, int(passes))
            if edge_id is None and lon is not None and lat is not None:
                edge_id, _ = cg.nearest_edge(self.graph, lon, lat, required_only=True)
            elif edge_id is None and street_name:
                cands = self._street_candidates(street_name)
                if not cands:
                    raise SimNotFound(f"Улица «{street_name}» не найдена")
                edge_id = self.task_by_id[self._rng.choice(sorted(cands))].edge_ids[0]
            elif edge_id is None:
                done = [t for t in self.tasks if t.status == TaskStatus.done.value]
                pool = done or [t for t in self.tasks if t.status != TaskStatus.cancelled.value]
                if not pool:
                    raise SimConflict("Нет участков для заявки")
                edge_id = self._rng.choice(sorted(pool, key=lambda t: t.id)).edge_ids[0]
            try:
                d = cg.edge_data(self.graph, edge_id)
            except (KeyError, ValueError) as e:
                raise SimNotFound(f"Ребро {edge_id} не найдено") from e
            task = self.task_by_id.get(d.get("task_id") or "")
            reopened = False
            if task is None:
                u, w, _ = cg.parse_edge_id(edge_id)
                eids = [edge_id] + ([d["reverse_edge_id"]] if d.get("reverse_edge_id") else [])
                task = Task(id=self._new_task_id(), edge_ids=eids, u=u, v=w, street_name=d.get("name"),
                            length_m=float(d["length"]), passes=passes, priority=priority, source="request")
                self.tasks.append(task)
                self.task_by_id[task.id] = task
                for e in eids:
                    ed = cg.edge_data(self.graph, e)
                    self._edge_task0.setdefault(e, ed.get("task_id"))
                    ed["task_id"] = task.id
                if task.street_name:
                    self._street_index = None
            elif task.status in (TaskStatus.done.value, TaskStatus.cancelled.value):
                task.passes = task.passes_done + passes
                task.status, task.assigned_vehicle = TaskStatus.pending.value, None
                task.priority, task.source, reopened = priority, "request", True
            else:
                task.priority = max(task.priority, priority)
            self._refresh_assignments()
            ev = self.events.add(self.t, EventType.new_request,
                                 {"task_id": task.id, "edge_id": edge_id, "priority": task.priority,
                                  "reopened": reopened},
                                 ev_txt.request_text(task.street_name or d.get("name"), task.id))
            self._after_event(ev)
            return ev

    def mark_task_done(self, task_id: str, vehicle_id: str) -> Event:
        """Driver button. Marks done; the vehicle just keeps driving."""
        with self._lock:
            task = self.task_by_id.get(task_id)
            if task is None:
                raise SimNotFound(f"Участок {task_id} не найден")
            if vehicle_id not in self.vehicles:
                raise SimNotFound(f"Машина {vehicle_id} не найдена")
            if task.status in (TaskStatus.done.value, TaskStatus.cancelled.value):
                raise SimConflict(f"Участок {task_id} уже закрыт")
            task.passes_done, task.status, task.assigned_vehicle = task.passes, TaskStatus.done.value, vehicle_id
            return self.events.add(self.t, EventType.task_done,
                                   {"task_id": task_id, "vehicle_id": vehicle_id, "manual": True},
                                   ev_txt.task_done_text(vehicle_id, task_id))

    def _problem_task(self, v: Vehicle) -> Task | None:
        """Task the vehicle is working on now, else the next planned service task."""
        closed = (TaskStatus.done.value, TaskStatus.cancelled.value)
        t = self.task_by_id.get(v.current_task_id or "")
        if t is not None and t.status not in closed:
            return t
        for st in v.route[v.route_index:]:
            t = self.task_by_id.get(st.task_id or "") if st.kind == _SVC else None
            if t is not None and t.status not in closed:
                return t
        return None

    def report_problem(self, vehicle_id: str, kind: str, comment: str | None = None) -> Event:
        """Driver problem report. breakdown|stuck -> like inject_breakdown; blocked -> current task is
        cancelled (skipped) and a replan is triggered; already_clean -> task marked done; other -> log only."""
        with self._lock:
            v = self.vehicles.get(vehicle_id)
            if v is None:
                raise SimNotFound(f"Машина {vehicle_id} не найдена")
            note = f" ({comment.strip()})" if comment and comment.strip() else ""
            if kind in ("breakdown", "stuck"):
                return self.inject_breakdown(vehicle_id)
            if kind in ("blocked", "already_clean"):
                task = self._problem_task(v)
                if task is None or task.status in (TaskStatus.done.value, TaskStatus.cancelled.value):
                    raise SimConflict(f"У {vehicle_id} нет активного участка")
                if kind == "already_clean":
                    return self.mark_task_done(task.id, vehicle_id)
                task.status, task.assigned_vehicle = TaskStatus.cancelled.value, None
                if v.current_task_id == task.id:
                    v.current_task_id = None
                self._refresh_assignments()
                ev = self.events.add(self.t, EventType.info,
                                     {"vehicle_id": vehicle_id, "kind": kind, "task_id": task.id,
                                      "comment": comment},
                                     f"{vehicle_id}: проезда нет, участок {task.id} пропущен{note}")
                self._after_event(ev)
                return ev
            if kind == "other":
                return self.events.add(self.t, EventType.info,
                                       {"vehicle_id": vehicle_id, "kind": kind, "comment": comment},
                                       f"{vehicle_id}: сообщение водителя{note or ''}")
            raise ValueError(f"unknown problem kind {kind}")

    # ------------------------------------------------------------------ replanning
    @property
    def has_pending_replans(self) -> bool:
        return bool(self._replan_queue)

    def run_pending_replans(self) -> int:
        """Async mode worker (call in a thread executor). Coalesces queued events: only the newest
        one becomes a proposal. Returns number of proposals created."""
        n = 0
        while True:
            with self._lock:
                if not self._replan_queue:
                    return n
                ev = self._replan_queue[-1]
                self._replan_queue.clear()
                job = self._prepare(ev)
            try:
                plan = self._solve(job)
                with self._lock:
                    if self._replan_queue:      # a newer event arrived while solving: this plan is stale
                        continue
                    self._finish(job, plan)
                    n += 1
            except Exception:
                log.exception("replan failed")
                with self._lock:
                    self.events.add(self.t, EventType.info, {"error": True},
                                    "Не удалось построить новый план")

    def replan(self, reason_event: Event) -> ReplanProposal:
        """Build proposal: remaining tasks + current positions -> solve_fn; diff vs current plan;
        metrics_before/after for the remaining work; auto_apply_at = now+delay if demo_auto_apply."""
        with self._lock:
            job = self._prepare(reason_event)
        plan = self._solve(job)
        with self._lock:
            return self._finish(job, plan)

    def resolve(self, weights: ObjectiveWeights | None = None, time_limit_s: float | None = None) -> Plan:
        """POST /plan/solve: re-optimise from the current state and apply immediately (no proposal)."""
        with self._lock:
            ev = self.events.add(self.t, EventType.info, {"action": "resolve"},
                                 "Диспетчер запустил перепланирование")
            self._expire_pending()
            job = self._prepare(ev, weights, time_limit_s)
        plan = self._solve(job)
        with self._lock:
            prop = self._finish(job, plan, auto_apply=False)
            return self.apply_proposal(prop.id)

    def _service_ids(self, steps: list[RouteStep]) -> list[str]:
        seen: dict[str, None] = {}
        for s in steps:
            if s.kind == _SVC and s.task_id:
                task = self.task_by_id.get(s.task_id)
                if task and task.status in (TaskStatus.pending.value, TaskStatus.assigned.value):
                    seen.setdefault(s.task_id)
        return list(seen)

    def _remaining_steps(self, v: Vehicle) -> list[RouteStep]:
        rt = self.rt[v.id]
        steps = v.route[v.route_index:]
        if steps and rt.entered and rt.mode != "unload" and rt.length > 0 and rt.offset > 0:
            f = max(0.0, 1.0 - rt.offset / rt.length)
            s0 = steps[0]
            steps = [s0.model_copy(update={"length_m": s0.length_m * f, "fuel_l": s0.fuel_l * f})] + steps[1:]
        return steps

    def _prepare(self, ev: Event | None, weights: ObjectiveWeights | None = None,
                 time_limit_s: float | None = None) -> _Job:
        vehicles, start_pos = [], {}
        for v in self.vehicles.values():
            if not self._is_plow(v) or v.status == VehicleStatus.broken.value:
                continue
            rt = self.rt[v.id]
            carry = self._carry_step(v, rt)
            node = carry.to_node if carry is not None else rt.node
            pos = self._vehicle_pos(v, rt) if carry is not None else self._node_pos(rt.node)
            vehicles.append(v.model_copy(update={"start_node": node, "route": [], "route_index": 0,
                                                 "position": pos, "current_task_id": None}, deep=True))
            start_pos[v.id] = pos
        old_rem = {vid: self._remaining_steps(v) for vid, v in self.vehicles.items() if self._is_plow(v)}
        before = compute_metrics(Plan(id="before", routes=old_rem, plan_start_t=self.t),
                                 weights or self.config.weights,
                                 self.baseline.metrics if self.baseline else None)
        return _Job(event=ev, t=self.t, tasks=[t.model_copy(deep=True) for t in self.tasks],
                    vehicles=vehicles, start_positions=start_pos, weights=weights or self.config.weights,
                    time_limit_s=time_limit_s or self.config.solver_time_limit_s, old_remaining=old_rem,
                    old_tasks={vid: self._service_ids(s) for vid, s in old_rem.items()}, before=before)

    def _solve(self, job: _Job) -> Plan:
        """Runs WITHOUT the lock, on copies only."""
        t0 = time.perf_counter()
        try:
            return self._solve_inner(job)
        finally:
            log.info("replan solve: %d plows, %d tasks, limit %.1fs -> %.2fs", len(job.vehicles),
                     len(job.tasks), job.time_limit_s, time.perf_counter() - t0)

    def _solve_inner(self, job: _Job) -> Plan:
        if not job.vehicles:
            todo = [t.id for t in job.tasks if t.status in (TaskStatus.pending.value, TaskStatus.assigned.value)
                    and t.passes_done < t.passes]
            return Plan(id="tmp", weights=job.weights, unassigned_task_ids=todo)
        return self.solve_fn(self.graph, job.tasks, job.vehicles, weights=job.weights,
                             start_positions=job.start_positions, time_limit_s=job.time_limit_s)

    def _finish(self, job: _Job, plan: Plan, auto_apply: bool = True) -> ReplanProposal:
        shift = job.t - plan.plan_start_t
        for steps in plan.routes.values():
            for s in steps:
                s.t_start += shift
                s.t_end += shift
        plan.plan_start_t, plan.created_at, plan.weights = job.t, job.t, job.weights
        plan.id = self._next_plan_id()
        ref = self.baseline.metrics if self.baseline else None
        fill_plan_metrics(plan, ref)
        diffs = self._diffs(job, plan)
        self._proposal_seq += 1
        pid = f"R-{self._proposal_seq:04d}"
        ev = job.event
        changed = sum(len(d.added_task_ids) for d in diffs)
        k = sum(1 for d in diffs if d.added_task_ids or d.removed_task_ids)
        dkm = plan.metrics.service_km + plan.metrics.deadhead_km - job.before.service_km - job.before.deadhead_km
        reason = ((ev.text if ev else "Перепланирование") + f". Предлагается перераспределить {changed} "
                  f"участков между {k} машинами (пробег {ev_txt.fmt_signed_km(dkm)})")
        if plan.unassigned_task_ids:
            reason += f"; недостижимых участков: {len(plan.unassigned_task_ids)}"
        at = self.clock() + self.config.auto_apply_delay_s if (auto_apply and self.config.demo_auto_apply) else None
        prop = ReplanProposal(id=pid, reason_event_id=ev.id if ev else "", reason_text=reason,
                              created_at=job.t, diffs=diffs, metrics_before=job.before,
                              metrics_after=plan.metrics, new_plan=plan, auto_apply_at=at)
        self._expire_pending()
        self.proposals[pid] = prop
        self._pending_id = pid
        self.proposal_version += 1
        self.events.add(self.t, EventType.proposal_created, {"proposal_id": pid},
                        ev_txt.proposal_created_text(pid))
        return prop

    def _diffs(self, job: _Job, plan: Plan) -> list[VehicleDiff]:
        out: list[VehicleDiff] = []
        for vid in self.vehicles:
            old_steps = job.old_remaining.get(vid, [])
            new_steps = plan.routes.get(vid, [])
            old_ids = job.old_tasks.get(vid, [])
            new_ids = []
            seen = set()
            for s in new_steps:
                if s.kind == _SVC and s.task_id and s.task_id not in seen:
                    seen.add(s.task_id)
                    new_ids.append(s.task_id)
            old_set, new_set = set(old_ids), set(new_ids)
            old_km = sum(s.length_m for s in old_steps) / 1000.0
            new_km = sum(s.length_m for s in new_steps) / 1000.0
            of = old_steps[-1].t_end if old_steps else None
            nf = new_steps[-1].t_end if new_steps else None
            broken = self.vehicles[vid].status == VehicleStatus.broken.value
            d = VehicleDiff(vehicle_id=vid, added_task_ids=[i for i in new_ids if i not in old_set],
                            removed_task_ids=[i for i in old_ids if i not in new_set],
                            delta_km=new_km - old_km, old_finish_t=of, new_finish_t=nf,
                            delta_finish_min=((nf if nf is not None else job.t) -
                                              (of if of is not None else job.t)) / 60.0)
            if broken or d.added_task_ids or d.removed_task_ids or abs(d.delta_km) > 0.01 \
                    or abs(d.delta_finish_min) > 0.1:
                out.append(d)
        return out

    # ------------------------------------------------------------------ proposals
    @property
    def pending_proposal(self) -> ReplanProposal | None:
        with self._lock:
            p = self.proposals.get(self._pending_id) if self._pending_id else None
            if p is None or p.status != ProposalStatus.pending.value:
                return None
            left = max(0.0, p.auto_apply_at - self.clock()) if p.auto_apply_at is not None else None
            return p.model_copy(update={"auto_apply_in_s": left})

    def _get_pending(self, proposal_id: str) -> ReplanProposal:
        p = self.proposals.get(proposal_id)
        if p is None:
            raise SimNotFound(f"Предложение {proposal_id} не найдено")
        if p.status != ProposalStatus.pending.value:
            raise SimConflict(f"Предложение {proposal_id} уже не актуально ({p.status})")
        return p

    def apply_proposal(self, proposal_id: str) -> Plan:
        """Replace routes of vehicles (re-based on their present state), status -> applied,
        event plan_applied. SimNotFound if unknown, SimConflict if not pending."""
        with self._lock:
            p = self._get_pending(proposal_id)
            log.info("apply proposal %s (plan %s)", proposal_id, p.new_plan.id)
            p.status = ProposalStatus.applied.value
            p.auto_apply_at = None
            self._pending_id = None
            plan = p.new_plan.model_copy(deep=True)
            self.plan = plan
            self._install_plan(plan)
            self.plan_version += 1
            self.proposal_version += 1
            self.events.add(self.t, EventType.plan_applied, {"proposal_id": proposal_id},
                            ev_txt.plan_applied_text(proposal_id))
            return plan

    def reject_proposal(self, proposal_id: str) -> None:
        """Rejecting keeps the old routes. Tasks freed by a breakdown / created by a request stay
        'pending' (unplanned) until a later plan is applied or POST /plan/solve is called."""
        with self._lock:
            p = self._get_pending(proposal_id)
            p.status = ProposalStatus.rejected.value
            p.auto_apply_at = None
            self._pending_id = None
            self.proposal_version += 1
            self.events.add(self.t, EventType.proposal_rejected, {"proposal_id": proposal_id},
                            ev_txt.proposal_rejected_text(proposal_id))

    # ------------------------------------------------------------------ queries / metrics
    def drain_events(self) -> list[Event]:
        """New events since last call (for ws 'event' messages)."""
        return self.events.drain_new()

    def _remaining_metrics(self) -> Metrics:
        per = [metrics_from_steps(self._remaining_steps(v), self.t) for v in self.vehicles.values()
               if self._is_plow(v) and v.status != VehicleStatus.broken.value and v.route_index < len(v.route)]
        return Metrics(deadhead_km=sum(p.deadhead_km for p in per), service_km=sum(p.service_km for p in per),
                       total_time_min=max((p.total_time_min for p in per), default=0.0),
                       fuel_l=sum(p.fuel_l for p in per))

    def live_metrics(self) -> Metrics:
        """Actual so far. total_time_min = time until the last vehicle movement."""
        m = Metrics(deadhead_km=self._tot_dead_m / 1000.0, service_km=self._tot_svc_m / 1000.0,
                    total_time_min=self._last_active_t / 60.0, fuel_l=self._tot_fuel,
                    truck_empty_km=self._truck_empty_m / 1000.0, truck_loaded_km=self._truck_loaded_m / 1000.0,
                    truck_fuel_l=self._truck_fuel)
        m.objective = objective(m, self.config.weights, self.baseline.metrics if self.baseline else None)
        return m

    def projected_metrics(self) -> Metrics:
        """Actual so far + remaining part of the current routes (whole-scenario projection)."""
        a, r = self.live_metrics(), self._remaining_metrics()
        end = max(self._last_active_t / 60.0, self.t / 60.0 + r.total_time_min if r.total_time_min else 0.0)
        m = Metrics(deadhead_km=a.deadhead_km + r.deadhead_km, service_km=a.service_km + r.service_km,
                    total_time_min=end, fuel_l=a.fuel_l + r.fuel_l, truck_empty_km=a.truck_empty_km,
                    truck_loaded_km=a.truck_loaded_km, truck_fuel_l=a.truck_fuel_l)
        m.objective = objective(m, self.config.weights, self.baseline.metrics if self.baseline else None)
        return m

    def comparison(self) -> MetricsComparison | None:
        with self._lock:
            if self.baseline is None:
                return None
            return compare_metrics(self.baseline.metrics, self.projected_metrics(), self.config.weights)

    def _summary(self) -> TaskSummary:
        s = TaskSummary()
        for t in self.tasks:
            s.total += 1
            setattr(s, t.status, getattr(s, t.status) + 1)
            if t.status != TaskStatus.cancelled.value:
                s.total_km += t.length_m * t.passes / 1000.0
            s.done_km += t.length_m * t.passes_done / 1000.0
        return s

    def _vehicle_view(self, v: Vehicle) -> Vehicle:
        start = max(0, v.route_index - 2)
        return v.model_copy(update={"route": v.route[start:], "route_index": v.route_index - start})

    def snapshot(self, consume_trail: bool = False) -> SimState:
        """consume_trail=True only for the single WS broadcast per tick: attaches and resets per-vehicle trails."""
        with self._lock:
            vs = [self._vehicle_view(v) for v in self.vehicles.values()]
            if consume_trail:
                for vv in vs:
                    rt = self.rt[vv.id]
                    vv.trail = list(rt.trail) if len(rt.trail) >= 2 else []
                    rt.trail.clear()
            proj = self.projected_metrics()
            base = self.baseline.metrics if self.baseline else None
            return SimState(
                t=self.t, clock=ev_txt.fmt_clock(self.t), speed=self.speed, running=self.running,
                vehicles=vs,
                task_statuses={t.id: t.status for t in self.tasks}, tasks_summary=self._summary(),
                metrics=self.live_metrics(), plan_metrics=proj, baseline_metrics=base,
                comparison=compare_metrics(base, proj, self.config.weights) if base else None,
                plan_id=self.plan.id, pending_proposal=self.pending_proposal,
                recent_events=self.events.recent(20))

    def tasks_list(self, status: str | None = None) -> list[Task]:
        with self._lock:
            return [t.model_copy() for t in self.tasks if status is None or t.status == status]

    def vehicles_list(self) -> list[Vehicle]:
        with self._lock:
            return [self._vehicle_view(v) for v in self.vehicles.values()]

    def driver_view(self, vehicle_id: str) -> DriverView:
        with self._lock:
            v = self.vehicles.get(vehicle_id)
            if v is None:
                raise SimNotFound(f"Машина {vehicle_id} не найдена")
            rem = v.route[v.route_index:] if v.status != VehicleStatus.broken.value else []
            cur = self.task_by_id.get(v.current_task_id) if v.current_task_id else None
            nxt: list[Task] = []
            seen = {cur.id} if cur else set()
            for s in rem:
                if s.kind == _SVC and s.task_id and s.task_id not in seen:
                    seen.add(s.task_id)
                    t = self.task_by_id.get(s.task_id)
                    if t and t.status in (TaskStatus.pending.value, TaskStatus.assigned.value):
                        nxt.append(t.model_copy())
                        if len(nxt) >= 5:
                            break
            feats: list[dict] = []
            run: list[list[float]] = []
            run_kind: str | None = None

            def flush() -> None:
                nonlocal run, run_kind
                if len(run) >= 2:
                    feats.append({"type": "Feature", "properties": {"kind": run_kind, "task_id": None},
                                  "geometry": {"type": "LineString", "coordinates": run}})
                run, run_kind = [], None
            acc_m = 0.0
            rt = self.rt[v.id]
            for i, s in enumerate(rem):
                if s.edge_id is None:
                    continue
                if acc_m > 3000:
                    break
                g = cg.edge_data(self.graph, s.edge_id)["geometry"]
                length = float(s.length_m or 0.0)
                if i == 0 and rt.entered and length > 0:
                    # current step: start EXACTLY at the vehicle position (remainder of the edge)
                    frac = max(0.0, min(1.0, rt.offset / length))
                    g = _cut_from(g, frac)
                    length = max(0.0, length - rt.offset)
                if len(g) < 2:
                    continue
                acc_m += length
                if s.kind == _SVC:
                    flush()
                    feats.append({"type": "Feature", "properties": {"kind": "service", "task_id": s.task_id},
                                  "geometry": {"type": "LineString", "coordinates": g}})
                else:
                    if run_kind not in (None, "deadhead"):
                        flush()
                    run_kind = "deadhead"
                    run.extend(g if not run else g[1:])
            flush()
            return DriverView(vehicle=self._vehicle_view(v), current_task=cur.model_copy() if cur else None,
                              next_tasks=nxt, route_geojson={"type": "FeatureCollection", "features": feats},
                              t=self.t, clock=ev_txt.fmt_clock(self.t))
