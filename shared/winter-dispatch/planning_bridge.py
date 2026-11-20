"""Adapt validated SnowOps actions to the dispatcher without invoking shell commands."""

from functools import lru_cache
from pathlib import Path
from threading import Lock
from time import perf_counter

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "pkc"
CASES = ("base_snowfall", "short_shift", "city_300")
CALCULATING = Lock()
MODE = {"deadhead": "d", "service": "s", "wait": "w", "return": "r"}


def package(graph, scenario, plans, times):
    """Road actions keep their OSM identity and their original solver times."""
    nodes = {}
    methods = {}
    for method, plan in plans.items():
        routes = []
        for route in plan.solver.routes:
            steps = []
            for action in route.actions:
                edge = graph.edges.get(action.edge_id)
                for node in (action.from_node, action.to_node):
                    nodes[node] = graph.nodes[node]
                steps.append([
                    action.start_s, action.end_s, MODE[action.mode], action.from_node, action.to_node,
                    edge.way_id if edge else None, action.task_id,
                ])
            routes.append({"vehicle": route.vehicle_id, "steps": steps})
        methods[method] = {
            "id": plan.plan_id, "status": plan.status, "termination": plan.solver.termination,
            "feasibility": plan.feasibility_status,
            "gap": plan.solver.pool_gap, "metrics": plan.metrics.model_dump(),
            "validation": plan.validation.model_dump(), "routes": routes, "computeMs": times.get(method),
        }
    tasks = []
    for task in scenario.tasks:
        edge = graph.edges[task.edge_id]
        for node in (edge.from_node, edge.to_node):
            nodes[node] = graph.nodes[node]
        tasks.append({
            "id": task.task_id, "way": edge.way_id, "from": edge.from_node, "to": edge.to_node,
            "name": edge.name, "priority": task.priority, "deadline": task.deadline_s,
            "passes": task.required_passes,
        })
    for vehicle in scenario.fleet:
        nodes[vehicle.depot_node] = graph.nodes[vehicle.depot_node]
    return {
        "version": 1, "case": scenario.scenario_id, "graphVersion": graph.version,
        "graphBounds": graph.audit.get("bbox"), "hash": scenario.input_hash, "seed": scenario.seed,
        "asOf": scenario.as_of.isoformat(), "origin": "validated_scenario",
        "roadSource": "OpenStreetMap", "tariff": scenario.tariff.model_dump(),
        "nodes": nodes, "tasks": tasks, "fleet": [v.model_dump() for v in scenario.fleet],
        "methods": methods,
    }


@lru_cache(maxsize=2)
def graph_for(filename):
    from snowops.osm import load_osm
    return load_osm(DATA / filename)


def calculate(case, seed):
    if case not in CASES or not 0 <= seed <= 2147483647:
        raise ValueError("Неизвестный сценарий или недопустимый seed.")
    if not CALCULATING.acquire(blocking=False):
        raise BlockingIOError("Идёт другой расчёт. Повторите после его завершения.")
    try:
        from snowops.pilot import scenario_factory
        from snowops.reporting import make_plan
        from snowops.solvers import GreedySolver, PoolSolver

        if case == "city_300":
            from snowops.infrastructure import Infrastructure
            graph = graph_for("city.osm.gz")
            scenario = Infrastructure(graph).scenario(300, seed=seed, scenario_id=case)
        else:
            graph = graph_for("network.osm.gz")
            scenario = scenario_factory(graph, seed=seed, scenario_id=case,
                                        shift_s=900 if case == "short_shift" else 1500)
        plans, times = {}, {}
        for name, solver in (("B0", GreedySolver()), ("O1", PoolSolver())):
            start = perf_counter()
            plans[name] = make_plan(graph, scenario, solver.solve(graph, scenario, 18))
            times[name] = round((perf_counter() - start) * 1000)
        return package(graph, scenario, plans, times)
    finally:
        CALCULATING.release()
