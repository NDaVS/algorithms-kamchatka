import networkx as nx

from core.models import Metrics, Plan, VehicleKind
from solver.cost import edge_cost, objective
from solver.metrics import compare_metrics, compute_metrics
from tests.synthetic import build_synthetic_city


def test_synthetic_city():
    c = build_synthetic_city()
    assert c.graph.number_of_nodes() == 156
    assert nx.is_strongly_connected(c.graph)
    assert c.tasks and all(t.edge_ids for t in c.tasks)
    assert len(c.vehicles) == 4


def test_cost_asymmetry():
    up = edge_cost(500, 8, "plow", "service")
    down = edge_cost(500, -8, "plow", "service")
    assert up.time_s > down.time_s and up.fuel_l > down.fuel_l
    assert edge_cost(500, 5, "truck", "deadhead", loaded=True).time_s > edge_cost(500, 5, "truck", "deadhead").time_s


def test_objective_and_compare():
    b = Metrics(deadhead_km=10, service_km=20, total_time_min=100, fuel_l=30)
    o = Metrics(deadhead_km=5, service_km=20, total_time_min=90, fuel_l=25)
    assert abs(objective(b, ref=b) - 1.0) < 1e-9
    cmp = compare_metrics(b, o)
    assert cmp.deadhead_pct == -50 and cmp.objective_pct < 0
    assert compute_metrics(Plan(id="p")).total_time_min == 0
