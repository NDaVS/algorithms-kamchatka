"""Solver tests on the synthetic city."""
import time
from collections import Counter

from core.graph import parse_edge_id
from core.models import Plan, Position, StepKind, TaskStatus, VehicleStatus
from solver.baseline import baseline_plows
from solver import cost as solver_cost
from solver.carp import solve_plows
from solver.metrics import compare_metrics
from tests.synthetic import build_synthetic_city


def _check_plan(city, plan: Plan, tasks, expected_vehicles, start_nodes=None):
    want = Counter()
    for t in tasks:
        if t.status in ("pending", "assigned") and t.passes_done < t.passes:
            want[t.id] += t.passes - t.passes_done
    got = Counter()
    for vid, steps in plan.routes.items():
        assert vid in expected_vehicles
        prev = None
        for s in steps:
            assert s.t_end >= s.t_start >= 0
            if prev is not None:
                assert prev.to_node == s.from_node, (vid, prev, s)
                assert abs(prev.t_end - s.t_start) < 1e-6
            if s.kind == StepKind.service.value:
                got[s.task_id] += 1
                u, v, _ = parse_edge_id(s.edge_id)
                assert (u, v) == (s.from_node, s.to_node)
            prev = s
        end = steps[-1].to_node if steps else None
        if steps:
            assert end == city.dump_node      # FINISH_AT=dump (default): common finish at the snow dump
            assert steps[-1].kind == StepKind.return_.value or steps[-1].kind == StepKind.service.value
            if start_nodes:
                assert steps[0].from_node == start_nodes[vid]
    unassigned = set(plan.unassigned_task_ids)
    for tid, n in want.items():
        if tid not in unassigned:
            assert got[tid] == n, tid
    assert set(got) <= set(want)


def test_solve_beats_baseline():
    city = build_synthetic_city()
    ids = {v.id for v in city.vehicles}
    base = baseline_plows(city.graph, city.tasks, city.vehicles)
    t0 = time.perf_counter()
    opt = solve_plows(city.graph, city.tasks, city.vehicles, time_limit_s=5)
    dt = time.perf_counter() - t0
    _check_plan(city, base, city.tasks, ids)
    _check_plan(city, opt, city.tasks, ids)
    assert base.solver == "baseline" and opt.solver == "carp"
    assert not opt.unassigned_task_ids and not base.unassigned_task_ids
    cmp = compare_metrics(base.metrics, opt.metrics, opt.weights)
    print(f"\nbaseline: dead={base.metrics.deadhead_km:.2f} km  time={base.metrics.total_time_min:.1f} min  "
          f"fuel={base.metrics.fuel_l:.2f} l  service={base.metrics.service_km:.2f}")
    print(f"carp    : dead={opt.metrics.deadhead_km:.2f} km  time={opt.metrics.total_time_min:.1f} min  "
          f"fuel={opt.metrics.fuel_l:.2f} l  service={opt.metrics.service_km:.2f}  runtime={dt:.2f}s")
    print(f"pct: dead {cmp.deadhead_pct:.1f}%  time {cmp.time_pct:.1f}%  fuel {cmp.fuel_pct:.1f}%  obj {cmp.objective_pct:.1f}%")
    assert dt < 5.5
    assert opt.metrics.deadhead_km < 0.85 * base.metrics.deadhead_km
    assert cmp.objective_pct < 0


def test_deterministic():
    city = build_synthetic_city()
    a = solve_plows(city.graph, city.tasks, city.vehicles, time_limit_s=20, seed=3)
    b = solve_plows(city.graph, city.tasks, city.vehicles, time_limit_s=20, seed=3)
    assert a.metrics == b.metrics
    assert {k: [s.edge_id for s in v] for k, v in a.routes.items()} == {k: [s.edge_id for s in v] for k, v in b.routes.items()}


def test_replan_mid_edge_and_broken():
    city = build_synthetic_city()
    first = solve_plows(city.graph, city.tasks, city.vehicles, time_limit_s=2)
    # pretend first 30 service steps per vehicle done, vehicles stand mid-edge on the next step
    done = set()
    pos = {}
    for vid, steps in first.routes.items():
        idx = min(40, len(steps) - 2)
        for s in steps[:idx]:
            if s.kind == StepKind.service.value:
                done.add(s.task_id)
        cur = steps[idx]
        u, v, k = parse_edge_id(cur.edge_id)
        nd = city.graph.nodes[u]
        pos[vid] = Position(lon=nd["x"], lat=nd["y"], edge_id=cur.edge_id, offset_m=cur.length_m * 0.4)
    tasks = [t.model_copy(update={"status": TaskStatus.done, "passes_done": t.passes}) if t.id in done else t
             for t in city.tasks]
    vehicles = [v.model_copy(update={"status": VehicleStatus.broken}) if v.id == "П-03" else v
                for v in city.vehicles]
    plan = solve_plows(city.graph, tasks, vehicles, start_positions=pos, time_limit_s=2)
    assert "П-03" not in plan.routes and len(plan.routes) == 3
    start_nodes = {vid: parse_edge_id(pos[vid].edge_id)[0] for vid in plan.routes}
    _check_plan(city, plan, tasks, set(plan.routes), start_nodes)
    for vid, steps in plan.routes.items():
        s0 = steps[0]
        assert s0.kind == StepKind.deadhead.value and s0.edge_id == pos[vid].edge_id
        assert s0.length_m < city.graph[parse_edge_id(s0.edge_id)[0]][parse_edge_id(s0.edge_id)[1]][0]["length"]
    served = {s.task_id for st in plan.routes.values() for s in st if s.kind == StepKind.service.value}
    assert not (served & done)


def test_edge_cases():
    city = build_synthetic_city()
    assert solve_plows(city.graph, city.tasks, [], time_limit_s=1).routes == {}
    done = [t.model_copy(update={"status": TaskStatus.done}) for t in city.tasks]
    p = solve_plows(city.graph, done, city.vehicles, time_limit_s=1)
    for st in p.routes.values():  # no work: only deadhead/return steps (to the dump finish), or empty
        assert all(s.kind in (StepKind.deadhead.value, StepKind.return_.value) for s in st)
    few = solve_plows(city.graph, city.tasks[:2], city.vehicles, time_limit_s=1)
    _check_plan(city, few, city.tasks[:2], {v.id for v in city.vehicles})
    multi = [t.model_copy(update={"passes": 2}) if i < 5 else t for i, t in enumerate(city.tasks[:10])]
    pm = solve_plows(city.graph, multi, city.vehicles, time_limit_s=1)
    _check_plan(city, pm, multi, {v.id for v in city.vehicles})


def _bbox_area(city, steps):
    xs, ys = [], []
    for s in steps:
        if s.kind == StepKind.service.value:
            for n in (s.from_node, s.to_node):
                xs.append(city.graph.nodes[n]["x"])
                ys.append(city.graph.nodes[n]["y"])
    return (max(xs) - min(xs)) * (max(ys) - min(ys)) if xs else 0.0


def test_districts_compact_and_finish_at_dump(monkeypatch):
    monkeypatch.setattr(solver_cost, "FINISH_AT", "dump")
    city = build_synthetic_city()
    plan = solve_plows(city.graph, city.tasks, city.vehicles, time_limit_s=3)
    base = baseline_plows(city.graph, city.tasks, city.vehicles)
    for pl in (plan, base):
        for vid, steps in pl.routes.items():
            assert steps and steps[-1].to_node == city.dump_node, (pl.solver, vid)
    xs = [d["x"] for _, d in city.graph.nodes(data=True)]
    ys = [d["y"] for _, d in city.graph.nodes(data=True)]
    total = (max(xs) - min(xs)) * (max(ys) - min(ys))
    ratios = [_bbox_area(city, st) / total for st in plan.routes.values()]
    print("\nbbox area / city:", [round(r, 2) for r in ratios])
    assert sum(ratios) / len(ratios) < 0.55 and max(ratios) < 0.8
    # no long deadhead hops between consecutive services
    longest = 0.0
    for steps in plan.routes.values():
        seen_service, hop = False, 0.0
        for s in steps:
            if s.kind == StepKind.deadhead.value:
                hop += s.length_m
            elif s.kind == StepKind.service.value:
                if seen_service:
                    longest = max(longest, hop)
                seen_service, hop = True, 0.0
    assert longest < 3000.0


def test_finish_at_depot(monkeypatch):
    monkeypatch.setattr(solver_cost, "FINISH_AT", "depot")
    city = build_synthetic_city()
    plan = solve_plows(city.graph, city.tasks, city.vehicles, time_limit_s=2)
    base = baseline_plows(city.graph, city.tasks, city.vehicles)
    for pl in (plan, base):
        for steps in pl.routes.values():
            assert steps[-1].to_node == city.depot_node
