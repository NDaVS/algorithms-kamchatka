"""Metrics computation from a Plan's routes (IMPLEMENTED, shared)."""
from __future__ import annotations

from core.models import Metrics, MetricsComparison, ObjectiveWeights, Plan, RouteStep, StepKind
from solver.cost import objective


def metrics_from_steps(steps: list[RouteStep], plan_start_t: float = 0.0) -> Metrics:
    """service_km = sum length of kind=service; deadhead_km = kind in (deadhead, return);
    fuel = sum step.fuel_l; total_time_min = (last t_end - plan_start_t)/60."""
    svc = sum(s.length_m for s in steps if s.kind == StepKind.service.value)
    dead = sum(s.length_m for s in steps if s.kind in (StepKind.deadhead.value, StepKind.return_.value))
    fuel = sum(s.fuel_l for s in steps)
    end = max((s.t_end for s in steps), default=plan_start_t)
    return Metrics(deadhead_km=dead / 1000, service_km=svc / 1000,
                   total_time_min=max(0.0, end - plan_start_t) / 60, fuel_l=fuel)


def compute_metrics(plan: Plan, weights: ObjectiveWeights | None = None,
                    ref: Metrics | None = None) -> Metrics:
    """Whole-fleet metrics of `plan` (makespan = max over vehicles). Does not mutate plan.
    Objective filled using `weights` (default plan.weights) and optional baseline `ref`."""
    w = weights or plan.weights
    per = [metrics_from_steps(r, plan.plan_start_t) for r in plan.routes.values()]
    m = Metrics(deadhead_km=sum(p.deadhead_km for p in per), service_km=sum(p.service_km for p in per),
                total_time_min=max((p.total_time_min for p in per), default=0.0),
                fuel_l=sum(p.fuel_l for p in per))
    m.objective = objective(m, w, ref)
    return m


def fill_plan_metrics(plan: Plan, baseline: Metrics | None = None) -> Plan:
    """Populate plan.vehicle_metrics and plan.metrics in place; returns plan."""
    plan.vehicle_metrics = {vid: metrics_from_steps(r, plan.plan_start_t) for vid, r in plan.routes.items()}
    plan.metrics = compute_metrics(plan, plan.weights, baseline)
    return plan


def _pct(new: float, old: float) -> float:
    return 0.0 if old == 0 else (new - old) / old * 100.0


def compare_metrics(baseline: Metrics, optimized: Metrics,
                    weights: ObjectiveWeights | None = None) -> MetricsComparison:
    """Negative pct = improvement over baseline."""
    w = weights or ObjectiveWeights()
    b = baseline.model_copy(update={"objective": objective(baseline, w, baseline)})
    o = optimized.model_copy(update={"objective": objective(optimized, w, baseline)})
    return MetricsComparison(baseline=b, optimized=o,
                             deadhead_pct=_pct(o.deadhead_km, b.deadhead_km),
                             time_pct=_pct(o.total_time_min, b.total_time_min),
                             fuel_pct=_pct(o.fuel_l, b.fuel_l),
                             objective_pct=_pct(o.objective, b.objective))
