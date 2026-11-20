"""Greedy nearest-unserved baseline. OWNER: solver agent."""
from __future__ import annotations

import networkx as nx

from core.models import ObjectiveWeights, Plan, Position, Task, Vehicle
from solver.carp import BIG, _active_plows, _Problem


def baseline_plows(graph: nx.MultiDiGraph, tasks: list[Task], vehicles: list[Vehicle],
                   weights: ObjectiveWeights | None = None,
                   start_positions: dict[str, Position] | None = None) -> Plan:
    """Naive reference: a shared pool of unserved jobs; the vehicle that becomes free first
    drives (shortest path by deadhead TIME) to the NEAREST unserved task endpoint and services
    that task in the direction it arrived, until nothing is left; then every vehicle returns to
    depot. Same signature/semantics/Plan shape as solve_plows (plan.solver = "baseline")."""
    weights = weights or ObjectiveWeights()
    plows = _active_plows(vehicles)
    if not plows:
        ids = sorted({t.id for t in tasks if t.status in ("pending", "assigned") and t.passes_done < t.passes})
        return Plan(id="P-0000", solver="baseline", weights=weights, routes={}, unassigned_task_ids=ids)
    P = _Problem(graph, tasks, plows, weights, start_positions, mode="time")
    nv = len(plows)
    avail = [P.t0[v] for v in range(nv)]
    cur = list(P.s)
    assign: list[list[tuple[int, int]]] = [[] for _ in range(nv)]
    remaining = list(range(P.n_jobs))
    Tm, opt = P.Tm, P.opt
    stuck = set()
    while remaining:
        vi = min((v for v in range(nv) if v not in stuck), key=lambda v: (avail[v], v), default=None)
        if vi is None:
            break
        row = Tm[cur[vi]]
        best = None
        for j in remaining:
            for o, op in enumerate(opt[j]):
                c = row[op[0]]
                if c < BIG and (best is None or c < best[0]):
                    best = (c, j, o)
        if best is None:
            stuck.add(vi)
            continue
        c, j, o = best
        op = opt[j][o]
        assign[vi].append((j, o))
        remaining.remove(j)
        avail[vi] += c + op[2]
        cur[vi] = op[1]
    leftover = {P.jobs_task[j].id for j in remaining}
    return P.make_plan(assign, "baseline", "P-0000", weights, leftover)
