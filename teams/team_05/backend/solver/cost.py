"""Shared cost model: travel time and fuel per edge, plus the combined objective.

THIS MODULE IS THE SINGLE SOURCE OF TRUTH. Solver, baseline, trucks and sim must all use it.

Regimes (derived from vehicle kind, step mode, loaded flag):
    plow_service   plow, plowing a required edge          base 15 km/h, 0.60 l/km
    plow_deadhead  plow, driving without plowing/return   base 35 km/h, 0.38 l/km
    truck_empty    dump truck, empty                       base 40 km/h, 0.35 l/km
    truck_loaded   dump truck, loaded (haul to dump)       base 25 km/h, 0.55 l/km
(Vehicle.service_speed_kmh / deadhead_speed_kmh override the base speeds.)

Slope s = edge.slope_pct for the direction of travel (+ uphill, - downhill).

Speed:   v = v_base * f(s)
    s > 0 : f = max(F_MIN, 1 - K_UP[regime] * s)         (loaded truck suffers most)
    s <= 0: f = min(F_DOWN_CAP[regime], 1 + K_DOWN * |s|) (plowing cannot speed up: cap 1.0)
Time:    t_s = length_m / (v_kmh / 3.6)
Fuel:    l = (length_m/1000) * L_PER_KM[regime] * g(s)
    s > 0 : g = 1 + K_FUEL_UP[regime] * s
    s <= 0: g = max(G_MIN, 1 - K_FUEL_DOWN * |s|)
Unload step: fixed UNLOAD_TIME_S, UNLOAD_FUEL_L.

Objective (lower is better):
    J = w_dead * deadhead_km/D0 + w_time * total_time_min/T0 + w_fuel * fuel_l/F0
where (D0,T0,F0) = reference metrics (baseline) if given, else DEFAULT_SCALES; so with a
baseline reference J(baseline)=sum(weights)=1.0 and J<1 means better than baseline.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from core.models import Metrics, ObjectiveWeights, Vehicle, VehicleKind

# Bump whenever solver output changes (part of the initial-plan cache key in app.main).
SOLVER_VERSION = "3-compact-districts-dump-finish"
# Where every vehicle ends its shift: "dump" (snow dump node) or "depot". Read at call time (tests may patch).
FINISH_AT = os.environ.get("SNEGOPLAN_FINISH_AT", "dump").strip().lower()

F_MIN = 0.35
K_DOWN = 0.01
G_MIN = 0.6
UNLOAD_TIME_S = 180.0
UNLOAD_FUEL_L = 0.3

BASE_SPEED_KMH = {"plow_service": 15.0, "plow_deadhead": 35.0, "truck_empty": 40.0, "truck_loaded": 25.0}
L_PER_KM = {"plow_service": 0.60, "plow_deadhead": 0.38, "truck_empty": 0.35, "truck_loaded": 0.55}
K_UP = {"plow_service": 0.050, "plow_deadhead": 0.025, "truck_empty": 0.030, "truck_loaded": 0.060}
F_DOWN_CAP = {"plow_service": 1.0, "plow_deadhead": 1.15, "truck_empty": 1.15, "truck_loaded": 1.05}
K_FUEL_UP = {"plow_service": 0.10, "plow_deadhead": 0.06, "truck_empty": 0.06, "truck_loaded": 0.12}
K_FUEL_DOWN = 0.02

# fallback normalisation when no baseline: (deadhead_km, total_time_min, fuel_l)
DEFAULT_SCALES = (10.0, 60.0, 20.0)


@dataclass(frozen=True)
class StepCost:
    time_s: float
    fuel_l: float
    speed_kmh: float


def regime(kind: str, mode: str, loaded: bool = False) -> str:
    """mode: 'service' | 'deadhead' (also 'return')."""
    if kind == VehicleKind.truck.value:
        return "truck_loaded" if loaded else "truck_empty"
    return "plow_service" if mode == "service" else "plow_deadhead"


def slope_speed_factor(slope_pct: float, reg: str) -> float:
    if slope_pct > 0:
        return max(F_MIN, 1.0 - K_UP[reg] * slope_pct)
    return min(F_DOWN_CAP[reg], 1.0 + K_DOWN * abs(slope_pct))


def slope_fuel_factor(slope_pct: float, reg: str) -> float:
    if slope_pct > 0:
        return 1.0 + K_FUEL_UP[reg] * slope_pct
    return max(G_MIN, 1.0 - K_FUEL_DOWN * abs(slope_pct))


def _base_speed(reg: str, vehicle: Vehicle | None) -> float:
    if vehicle is not None:
        if reg in ("plow_service", "truck_loaded"):
            return vehicle.service_speed_kmh
        return vehicle.deadhead_speed_kmh
    return BASE_SPEED_KMH[reg]


def edge_cost(length_m: float, slope_pct: float, kind: str, mode: str,
              loaded: bool = False, vehicle: Vehicle | None = None) -> StepCost:
    """Cost of traversing one directed edge. `kind` is 'plow'|'truck'; mode 'service'|'deadhead'."""
    reg = regime(kind, mode, loaded)
    v = _base_speed(reg, vehicle) * slope_speed_factor(slope_pct, reg)
    t = length_m / (v / 3.6)
    fuel = (length_m / 1000.0) * L_PER_KM[reg] * slope_fuel_factor(slope_pct, reg)
    return StepCost(time_s=t, fuel_l=fuel, speed_kmh=v)


def vehicle_edge_cost(vehicle: Vehicle, length_m: float, slope_pct: float, mode: str,
                      loaded: bool | None = None) -> StepCost:
    """Convenience wrapper using the vehicle's own speed parameters."""
    return edge_cost(length_m, slope_pct, vehicle.kind, mode,
                     vehicle.loaded if loaded is None else loaded, vehicle)


def unload_cost() -> StepCost:
    return StepCost(time_s=UNLOAD_TIME_S, fuel_l=UNLOAD_FUEL_L, speed_kmh=0.0)


def objective(metrics: Metrics, weights: ObjectiveWeights | None = None,
              ref: Metrics | None = None) -> float:
    """Weighted combined objective (lower is better). See module docstring."""
    w = weights or ObjectiveWeights()
    if ref is not None:
        d0 = ref.deadhead_km or 1e-9
        t0 = ref.total_time_min or 1e-9
        f0 = ref.fuel_l or 1e-9
    else:
        d0, t0, f0 = DEFAULT_SCALES
    return (w.deadhead * metrics.deadhead_km / d0
            + w.time * metrics.total_time_min / t0
            + w.fuel * metrics.fuel_l / f0)


def finish_node(graph, vehicle: Vehicle) -> int:
    """Node where `vehicle` ends the shift: the dump node when FINISH_AT == "dump" (and the graph has one),
    else the vehicle's depot."""
    if FINISH_AT == "dump":
        dn = graph.graph.get("dump_node")
        if dn is not None and dn in graph.nodes:
            return dn
    return vehicle.depot_node
