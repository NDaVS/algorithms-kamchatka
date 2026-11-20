"""Dump-truck planning (phase 2).

Snow loading points are the endpoints of plowed tasks on major streets (or tasks marked high-volume:
priority >= 2 or passes >= 2). A truck does trips  load point -> dump (Снегоотвал) -> next load point.
A trip may only start after the plow served the task, so pickups are processed in order of the
plow's service time (`ready_t`). Greedy: every pickup goes to the truck whose EMPTY leg is cheapest
(travel time from solver.cost, so uphill slopes are penalised); the load endpoint is the one that
minimises  empty_time(to endpoint) + loaded_time(endpoint -> dump)  (loaded uphill is penalised most).
Only networkx/pydantic/solver.cost are used (no fastapi).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import networkx as nx

from core import graph as cg
from core.models import ObjectiveWeights, Plan, Position, RouteStep, StepKind, Task, TaskStatus, Vehicle
from solver.cost import finish_node, unload_cost, vehicle_edge_cost
from solver.metrics import fill_plan_metrics

log = logging.getLogger("snegoplan.trucks")

MAJOR_HIGHWAYS = {"motorway", "trunk", "primary", "secondary", "tertiary",
                  "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
MAX_PICKUPS = 24


@dataclass
class Trip:
    pickup_edge_id: str
    ref: str | None                 # e.g. task id the pickup belongs to
    ready_t: float                  # earliest time the pile exists (plow finished the task)
    load_node: int
    steps: list[RouteStep] = field(default_factory=list)   # empty leg + loaded haul + unload


def select_pickup_tasks(graph: nx.MultiDiGraph, tasks: list[Task], max_n: int = MAX_PICKUPS) -> list[Task]:
    """Tasks that produce snow piles worth hauling: major streets or high-volume (priority/passes).
    Fallback (no major streets in the data): the longest ~20% of tasks. Deterministic, capped."""
    live = [t for t in tasks if t.status != TaskStatus.cancelled.value]

    def major(t: Task) -> bool:
        try:
            hw = str(cg.edge_data(graph, t.edge_ids[0]).get("highway", ""))
        except Exception:  # noqa: BLE001
            return False
        return hw in MAJOR_HIGHWAYS

    sel = [t for t in live if major(t) or t.priority >= 2 or t.passes >= 2]
    if not sel and live:
        k = max(1, len(live) // 5)
        sel = sorted(live, key=lambda t: (-t.length_m, t.id))[:k]
    sel.sort(key=lambda t: (-t.length_m, t.id))
    return sorted(sel[:max_n], key=lambda t: t.id)


def _path_steps(graph, truck: Vehicle, a: int, b: int, loaded: bool, t: float):
    """(steps, end_time) for the cheapest path a->b; raises NetworkXNoPath."""
    out: list[RouteStep] = []
    for eid in cg.shortest_path_edges(graph, a, b, kind="truck", mode="deadhead", loaded=loaded):
        u, w, _ = cg.parse_edge_id(eid)
        d = cg.edge_data(graph, eid)
        c = vehicle_edge_cost(truck, float(d["length"]), d.get("slope_pct", 0.0), "deadhead", loaded)
        out.append(RouteStep(kind=StepKind.service if loaded else StepKind.deadhead, edge_id=eid, from_node=u,
                             to_node=w, length_m=float(d["length"]), loaded=loaded, t_start=t,
                             t_end=t + c.time_s, fuel_l=c.fuel_l))
        t += c.time_s
    return out, t


def _time(steps: list[RouteStep]) -> float:
    return sum(s.t_end - s.t_start for s in steps)


def plan_truck_trips(graph: nx.MultiDiGraph, pickups: list[tuple[str, float, str | None]],
                     trucks: list[Vehicle], dump_node: int,
                     start_nodes: dict[str, int] | None = None, free_at: dict[str, float] | None = None
                     ) -> dict[str, list[Trip]]:
    """pickups: [(edge_id, ready_t, ref)]. Returns per-truck ordered trips (no final return to depot).
    Unreachable pickups are skipped (logged)."""
    node = {t.id: (start_nodes or {}).get(t.id, t.start_node if t.start_node is not None else t.depot_node)
            for t in trucks}
    free = {t.id: (free_at or {}).get(t.id, 0.0) for t in trucks}
    trips: dict[str, list[Trip]] = {t.id: [] for t in trucks}
    if not trucks:
        return trips
    for edge_id, ready, ref in sorted(pickups, key=lambda p: (p[1], p[0])):
        try:
            u, w, _ = cg.parse_edge_id(edge_id)
        except Exception:  # noqa: BLE001
            continue
        best = None   # (key, truck, load_node, empty_steps, haul_steps)
        for tr in trucks:
            for ln in {u, w}:
                try:
                    es, _ = _path_steps(graph, tr, node[tr.id], ln, False, 0.0)
                    hs, _ = _path_steps(graph, tr, ln, dump_node, True, 0.0)
                except nx.NetworkXNoPath:
                    continue
                et = _time(es)
                # primary: empty leg time (uphill-penalised); then whole trip (loaded uphill penalty), then idle truck
                key = (round(et, 3), round(et + _time(hs), 3), free[tr.id], tr.id, ln)
                if best is None or key < best[0]:
                    best = (key, tr, ln, es, hs)
        if best is None:
            log.warning("pickup %s unreachable for all trucks; skipped", edge_id)
            continue
        _, tr, ln, es, hs = best
        et = _time(es)
        t0 = max(free[tr.id], ready - et)         # do not arrive before the plow has finished
        es, t = _path_steps(graph, tr, node[tr.id], ln, False, t0)
        hs, t = _path_steps(graph, tr, ln, dump_node, True, max(t, ready))
        uc = unload_cost()
        un = RouteStep(kind=StepKind.unload, edge_id=None, from_node=dump_node, to_node=dump_node, length_m=0.0,
                       loaded=False, t_start=t, t_end=t + uc.time_s, fuel_l=uc.fuel_l)
        trips[tr.id].append(Trip(pickup_edge_id=edge_id, ref=ref, ready_t=ready, load_node=ln,
                                 steps=es + hs + [un]))
        node[tr.id], free[tr.id] = dump_node, un.t_end
    return trips


def plan_trucks(graph: nx.MultiDiGraph, pickup_edge_ids: list[str], trucks: list[Vehicle],
                dump_node: int, weights: ObjectiveWeights | None = None,
                start_positions: dict[str, Position] | None = None,
                time_limit_s: float = 3.0, ready_times: dict[str, float] | None = None) -> Plan:
    """Assign snow-pile pickups (edges, in order of plow service) to trucks. Per pickup: empty travel
    (deadhead, loaded=False) -> loaded haul edges (kind=service, loaded=True) -> unload step at dump
    (edge_id None, solver.cost.UNLOAD_TIME_S) -> next pickup. Ends at the finish node (dump by default, else depot).
    `ready_times` (edge_id -> sim time the plow finishes it) delays trips; default: list order, ready 0."""
    start_nodes: dict[str, int] = {}
    for vid, pos in (start_positions or {}).items():
        if pos is not None and pos.edge_id:
            start_nodes[vid] = cg.parse_edge_id(pos.edge_id)[1]
    rt = ready_times or {}
    pickups = [(e, rt.get(e, float(i)), None) for i, e in enumerate(pickup_edge_ids)] if not rt else \
        [(e, rt.get(e, 0.0), None) for e in pickup_edge_ids]
    trips = plan_truck_trips(graph, pickups, trucks, dump_node, start_nodes)
    plan = Plan(id="tmp", solver="trucks", weights=weights or ObjectiveWeights())
    for tr in trucks:
        steps = [s for trip in trips[tr.id] for s in trip.steps]
        node = steps[-1].to_node if steps else start_nodes.get(tr.id, tr.start_node if tr.start_node is not None
                                                                else tr.depot_node)
        t = steps[-1].t_end if steps else 0.0
        fin = finish_node(graph, tr)      # dump by default: nothing to drive after the last unload
        if node != fin:
            try:
                for eid in cg.shortest_path_edges(graph, node, fin, kind="truck", mode="deadhead",
                                                  loaded=False):
                    u, w, _ = cg.parse_edge_id(eid)
                    d = cg.edge_data(graph, eid)
                    c = vehicle_edge_cost(tr, float(d["length"]), d.get("slope_pct", 0.0), "deadhead", False)
                    steps.append(RouteStep(kind=StepKind.return_, edge_id=eid, from_node=u, to_node=w,
                                           length_m=float(d["length"]), t_start=t, t_end=t + c.time_s,
                                           fuel_l=c.fuel_l))
                    t += c.time_s
            except nx.NetworkXNoPath:
                log.warning("truck %s cannot return to depot", tr.id)
        plan.routes[tr.id] = steps
    return fill_plan_metrics(plan)
