"""SimEngine tests (no fastapi needed). Uses the real solver + graph helpers."""
from __future__ import annotations

import pytest

from core.models import AppConfig, EventType, ProposalStatus, SimState, TaskStatus
from sim.engine import SimConflict, SimEngine, SimNotFound
from solver.baseline import baseline_plows
from solver.carp import solve_plows
from tests.synthetic import build_synthetic_city


class FakeClock:
    def __init__(self): self.now = 1000.0
    def __call__(self): return self.now


def make(auto=False, delay=8.0, clock=None):
    city = build_synthetic_city()
    cfg = AppConfig(demo_auto_apply=auto, auto_apply_delay_s=delay, solver_time_limit_s=1.0)
    def solve(*a, **k):
        k["time_limit_s"] = min(k.get("time_limit_s", 1.0), 1.0)
        return solve_plows(*a, **k)
    kw = {"clock": clock} if clock else {}
    return SimEngine(city.graph, city.tasks, city.vehicles, cfg, solve, baseline_plows, **kw)


@pytest.fixture(scope="module")
def eng0():
    return make()


def run(e, seconds_real=1.0, n=1):
    for _ in range(n):
        e.tick(seconds_real)


def test_init_and_snapshot():
    e = make()
    s = e.snapshot()
    SimState.model_validate(s.model_dump())
    assert s.t == 0 and not s.running and s.clock == "06:00"
    assert e.plan.id and e.baseline is not None and s.comparison is not None
    for v in e.vehicles.values():
        for a, b in zip(v.route, v.route[1:]):
            assert a.to_node == b.from_node
    assert any(t.status == TaskStatus.assigned for t in e.tasks)
    e.tick(5)
    assert e.t == 0  # paused


def test_moves_and_positions():
    e = make()
    e.set_speed(10); e.start()
    run(e, 2.0, 5)
    assert e.t == pytest.approx(100)
    moved = [v for v in e.vehicles.values() if v.distance_km > 0]
    assert moved and all(v.position.edge_id for v in moved if v.status != "at_depot")
    m = e.live_metrics()
    assert m.service_km + m.deadhead_km > 0


def test_run_to_completion():
    e = make()
    e.set_speed(60); e.start()
    for _ in range(3000):
        e.tick(2.0)
        if all(t.status == "done" for t in e.tasks) and all(
                v.route_index >= len(v.route) for v in e.vehicles.values()):
            break
    assert all(t.status == "done" for t in e.tasks)
    assert all(v.status in ("at_depot", "finished") for v in e.vehicles.values())
    evs = e.drain_events()
    assert sum(1 for x in evs if x.type == EventType.task_done) == len(e.tasks)
    assert e.comparison() is not None


def test_breakdown_replan_apply():
    e = make()
    e.set_speed(10); e.start(); run(e, 3.0, 10)
    vid = "П-02"
    own = [t.id for t in e.tasks if t.assigned_vehicle == vid]
    ev = e.inject_breakdown(vid)
    assert ev.type == EventType.breakdown and ev.text.startswith("Поломка: П-02 остановился")
    assert e.vehicles[vid].status == "broken"
    p = e.pending_proposal
    assert p and p.status == "pending" and p.reason_text
    assert any(d.vehicle_id == vid and d.removed_task_ids for d in p.diffs)
    with pytest.raises(SimConflict):
        e.inject_breakdown(vid)
    plan = e.apply_proposal(p.id)
    assert e.pending_proposal is None and vid not in plan.routes
    assert e.vehicles[vid].route_index >= len(e.vehicles[vid].route)
    for v in e.vehicles.values():
        for a, b in zip(v.route[v.route_index:], v.route[v.route_index + 1:]):
            assert a.to_node == b.from_node
    with pytest.raises(SimConflict):
        e.apply_proposal(p.id)
    e.set_speed(60)
    for _ in range(3000):
        e.tick(2.0)
        if all(t.status == "done" for t in e.tasks):
            break
    assert all(t.status == "done" for t in e.tasks)


def test_request_variants_reject_supersede():
    e = make()
    e.start()
    with pytest.raises(SimNotFound):
        e.inject_breakdown("X-99")
    ev1 = e.inject_request(street_name="Лесная")
    p1 = e.pending_proposal
    assert ev1.type == EventType.new_request and "расчистить" in ev1.text
    ev2 = e.inject_request(edge_id=e.tasks[3].edge_ids[0])
    assert e.proposals[p1.id].status == ProposalStatus.expired
    p2 = e.pending_proposal
    e.reject_proposal(p2.id)
    assert e.pending_proposal is None
    ev3 = e.inject_request()
    tid = ev3.payload["task_id"]
    assert e.task_by_id[tid].priority >= 1
    # new task on a non-task edge
    n0 = len(e.tasks)
    yard = next(d["edge_id"] for _, _, d in e.graph.edges(data=True) if not d.get("task_id"))
    ev4 = e.inject_request(edge_id=yard, priority=3)
    assert len(e.tasks) == n0 + 1 and e.task_by_id[ev4.payload["task_id"]].source == "request"
    lon = e.graph.nodes[5]["x"]; lat = e.graph.nodes[5]["y"]
    e.inject_request(lon=lon, lat=lat)


def test_reopen_done_task():
    e = make()
    t = e.tasks[0]
    e.mark_task_done(t.id, "П-01")
    assert t.status == "done"
    with pytest.raises(SimConflict):
        e.mark_task_done(t.id, "П-01")
    e.inject_request(edge_id=t.edge_ids[0])
    assert t.status in ("pending", "assigned") and t.passes == 2 and t.source == "request"
    e.apply_proposal(e.pending_proposal.id)
    assert t.status == "assigned"


def test_auto_apply():
    clk = FakeClock()
    e = make(auto=True, delay=8, clock=clk)
    e.start()
    e.inject_breakdown("П-01")
    p = e.pending_proposal
    assert p.auto_apply_in_s == pytest.approx(8)
    e.tick(0.1)
    assert e.pending_proposal is not None
    clk.now += 9
    e.tick(0.1)
    assert e.pending_proposal is None
    assert any(x.type == EventType.plan_applied for x in e.events.all())


def test_async_mode_and_resolve():
    e = make()
    e.async_replan = True
    e.inject_breakdown("П-03")
    assert e.has_pending_replans and e.pending_proposal is None
    assert e.run_pending_replans() == 1
    assert e.pending_proposal is not None
    plan = e.resolve()
    assert e.pending_proposal is None and "П-03" not in plan.routes


def test_driver_view_and_reset():
    e = make()
    e.set_speed(10); e.start(); run(e, 3.0, 10)
    dv = e.driver_view("П-01")
    assert dv.route_geojson["features"] and dv.vehicle.id == "П-01"
    e.reset()
    assert e.t == 0 and not e.running and all(t.passes_done == 0 for t in e.tasks)
    assert e.snapshot().plan_id
    with pytest.raises(SimNotFound):
        e.driver_view("Z")


def test_trucks_haul_after_plow_and_metrics_separate():
    city = build_synthetic_city(n_trucks=2)
    cfg = AppConfig(demo_auto_apply=False, solver_time_limit_s=1.0, n_trucks=2)
    e = SimEngine(city.graph, city.tasks, city.vehicles, cfg,
                  lambda *a, **k: solve_plows(*a, **{**k, "time_limit_s": 1.0}), baseline_plows)
    assert {v.id for v in e.vehicles.values() if v.kind == "truck"} == {"С-01", "С-02"}
    assert sum(len(q) for q in e._truck_queue.values()) > 0
    e.set_speed(60)
    e.start()
    unloaded = False
    for _ in range(4000):
        e.tick(1.0)
        if any(ev.text.endswith("выгрузил снег на снегоотвале") for ev in e.events.all()):
            unloaded = True
            break
    assert unloaded
    m = e.live_metrics()
    assert m.truck_empty_km > 0 and m.truck_loaded_km > 0
    # every dispatched trip's pickup task was already done when the truck left
    for tid in e._truck_served:
        assert e.task_by_id[tid].status == TaskStatus.done
