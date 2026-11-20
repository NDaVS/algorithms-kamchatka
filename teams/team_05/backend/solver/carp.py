"""CARP heuristic for plows. OWNER: solver agent. See CONTRACTS.md section 3.

Pipeline (solve_plows):
  1. _Problem: expand tasks into one-pass "jobs" with directed service options (any edge of the task),
     all-pairs shortest deadhead paths among relevant nodes (scipy Dijkstra from those nodes only;
     time / length / fuel along the same paths via pointer jumping on the predecessor trees).
  2. compact districts: weighted k-means + power-diagram balancing on job midpoints, then repair so every
     district is connected on the road graph (small fragments go to the neighbouring district); matched
     to vehicle starts (Hungarian).
  3. per-district path scanning (6 rules + randomised restarts) strongly preferring the nearest unserved
     task, continuing along the same street / direction, penalising U-turns and sharp turns.
  4. local search on the whole solution: neighbour relocate, direction flip, swap, intra-route 2-opt, or-opt,
     inter-route moves only when compact (job adjacent to the receiving district), minimising
     w_d*sum(deadhead_km)/D0 + w_t*makespan/T0 + w_f*sum(fuel)/F0 + turn / long-hop penalties.
  Every vehicle ends at cost.finish_node (snow dump by default, see cost.FINISH_AT).
  5. expansion to edge-level contiguous RouteSteps with exact costs from solver.cost.

Deterministic for fixed inputs + `seed` whenever the stall criterion ends the search before
`time_limit_s` (the time limit is only a safety cap).
"""
from __future__ import annotations

import heapq
import math
import random
import time

import networkx as nx
import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from core.graph import haversine_m, parse_edge_id
from core.models import (ObjectiveWeights, Plan, Position, RouteStep, StepKind, Task, TaskStatus,
                         Vehicle, VehicleKind, VehicleStatus)
from scipy.sparse.csgraph import connected_components

from solver.cost import DEFAULT_SCALES, edge_cost, finish_node, vehicle_edge_cost
from solver.metrics import fill_plan_metrics

BIG = 1e9
# route-quality constants (metres unless noted)
LOW_CLASS = {"service", "living_street", "track", "path"}   # avoid for deadheading
LOW_CLASS_MULT = 2.0
UTURN_M = 150.0        # penalty (in deadhead-metre equivalents) for reversing onto the same street
SHARP_M = 40.0         # penalty scale for sharp turns (heading dot < -0.3)
HOP2_W = 0.25          # weight of sum(hop_km^2) between consecutive services
HOP_EXC_W = 3.0        # weight of (max hop - HOP_MAX_M) in km
HOP_MAX_M = 1500.0
ADJ_M = 150.0          # two jobs closer than this are "adjacent" (district connectivity)
NEAR_M = 400.0         # inter-district moves only to a job at most this close
_ACTIVE = (TaskStatus.pending.value, TaskStatus.assigned.value)


# ----------------------------------------------------------------------------- helpers
def _nearest_node(graph: nx.MultiDiGraph, lon: float, lat: float) -> int:
    best, bd = None, float("inf")
    for n, d in graph.nodes(data=True):
        dd = haversine_m(lon, lat, d["x"], d["y"])
        if dd < bd:
            best, bd = n, dd
    return best


def start_node_for(graph: nx.MultiDiGraph, vehicle: Vehicle, pos: Position | None) -> int:
    """Node from which a vehicle's new route begins (end node of its current edge if mid-edge)."""
    if pos is None:
        return vehicle.start_node if vehicle.start_node is not None else vehicle.depot_node
    if pos.edge_id:
        try:
            u, v, k = parse_edge_id(pos.edge_id)
            if graph.has_edge(u, v, k):
                return u if pos.offset_m <= 1e-6 else v
        except Exception:  # noqa: BLE001
            pass
    return _nearest_node(graph, pos.lon, pos.lat)


def _active_plows(vehicles: list[Vehicle]) -> list[Vehicle]:
    return [v for v in vehicles if v.kind == VehicleKind.plow.value and v.status != VehicleStatus.broken.value]


class _Problem:
    """Preprocessed instance shared by baseline and CARP solver."""

    def __init__(self, graph: nx.MultiDiGraph, tasks: list[Task], plows: list[Vehicle],
                 weights: ObjectiveWeights, start_positions: dict[str, Position] | None,
                 mode: str = "composite") -> None:
        self.graph, self.plows, self.w = graph, plows, weights
        self.mode = mode
        ref = plows[0]
        self.ref = ref
        sp = start_positions or {}
        wd, wt, wf = (weights.deadhead, weights.time, weights.fuel)
        d0, t0_, f0_ = DEFAULT_SCALES

        # ---- edges (best parallel edge per (u,v) under the path weight)
        nodes = list(graph.nodes)
        self.nodes = nodes
        self.nid = {n: i for i, n in enumerate(nodes)}
        N = len(nodes)
        self.N = N
        best: dict[tuple[int, int], tuple] = {}
        for u, v, k, d in graph.edges(keys=True, data=True):
            ln, sl = d["length"], d.get("slope_pct", 0.0)
            c = edge_cost(ln, sl, "plow", "deadhead", vehicle=ref)
            if mode == "time":
                w = c.time_s
            else:
                w = wd * ln / 1000.0 / d0 + wt * c.time_s / 60.0 / t0_ + wf * c.fuel_l / f0_
            hw = d.get("highway")
            if isinstance(hw, (list, tuple)):
                hw = hw[0] if hw else None
            if hw in LOW_CLASS:
                w *= LOW_CLASS_MULT
            w += 1e-9
            key = (self.nid[u], self.nid[v])
            if key not in best or w < best[key][0]:
                best[key] = (w, c.time_s, ln, c.fuel_l, d["edge_id"], sl)
        self.best = best
        keys = sorted(best)
        iu = np.array([k[0] for k in keys], dtype=np.int64)
        iv = np.array([k[1] for k in keys], dtype=np.int64)
        wv = np.array([best[k][0] for k in keys])
        self._codes = iu * N + iv
        self._tv = np.array([best[k][1] for k in keys])
        self._lv = np.array([best[k][2] for k in keys])
        self._fv = np.array([best[k][3] for k in keys])
        self.csr = csr_matrix((wv, (iu, iv)), shape=(N, N))

        # ---- vehicle starts
        self.start_node: list[int] = []
        self.rem_step: list[dict | None] = []
        self.t0: list[float] = []
        self.d0: list[float] = []
        self.f0: list[float] = []
        for veh in plows:
            pos = sp.get(veh.id)
            node = start_node_for(graph, veh, pos)
            rem = None
            tt = dd = ff = 0.0
            if pos is not None and pos.edge_id:
                try:
                    u, v, k = parse_edge_id(pos.edge_id)
                    if graph.has_edge(u, v, k) and pos.offset_m > 1e-6:
                        data = graph[u][v][k]
                        r_len = max(0.0, data["length"] - pos.offset_m)
                        if r_len > 1e-6:
                            rem = dict(edge_id=data["edge_id"], u=u, v=v, length=r_len,
                                       slope=data.get("slope_pct", 0.0))
                            c = edge_cost(r_len, rem["slope"], "plow", "deadhead", vehicle=ref)
                            tt, dd, ff = c.time_s, r_len, c.fuel_l
                except Exception:  # noqa: BLE001
                    rem = None
            self.start_node.append(node)
            self.rem_step.append(rem)
            self.t0.append(tt)
            self.d0.append(dd)
            self.f0.append(ff)

        # ---- jobs (one per remaining pass)
        raw_jobs: list[tuple[Task, list[tuple]]] = []
        for t in tasks:
            if t.status not in _ACTIVE or t.passes_done >= t.passes:
                continue
            opts = []
            for eid in t.edge_ids:
                try:
                    u, v, k = parse_edge_id(eid)
                except Exception:  # noqa: BLE001
                    continue
                if not graph.has_edge(u, v, k):
                    continue
                d = graph[u][v][k]
                opts.append((u, v, eid, d["length"], d.get("slope_pct", 0.0)))
            if not opts:
                self.__dict__.setdefault("_noopt", set()).add(t.id)
                continue
            for _ in range(t.passes - t.passes_done):
                raw_jobs.append((t, opts))

        depots = [finish_node(graph, v) for v in plows]   # shift end (dump or depot)
        self.depot_node = depots
        rel = set(self.start_node) | set(depots)
        for _, opts in raw_jobs:
            for o in opts:
                rel.add(o[0])
                rel.add(o[1])
        self.rnodes = sorted(rel)
        self.rid = {n: i for i, n in enumerate(self.rnodes)}
        self._all_pairs()
        self.s = [self.rid[n] for n in self.start_node]
        self.dep = [self.rid[n] for n in depots]

        # ---- job options (service cost on the reference vehicle) + feasibility
        self.jobs_task: list[Task] = []
        self.opt: list[list[tuple]] = []       # (a, b, svc_t, svc_f, svc_len, svc_c) in R-index space
        self.opt_edge: list[list[tuple]] = []  # (edge_id, u, v)
        self.unassigned: set[str] = set(self.__dict__.get("_noopt", set()))
        Cm = self.Cm
        for t, opts in raw_jobs:
            good, goode = [], []
            for (u, v, eid, ln, sl) in opts:
                a, b = self.rid[u], self.rid[v]
                ok = any(Cm[self.s[i]][a] < BIG and Cm[b][self.dep[i]] < BIG for i in range(len(plows)))
                if not ok:
                    continue
                c = edge_cost(ln, sl, "plow", "service", vehicle=ref)
                sc = wt * c.time_s / 60.0 / t0_ + wf * c.fuel_l / f0_
                good.append((a, b, c.time_s, c.fuel_l, ln, sc))
                goode.append((eid, u, v))
            if good:
                self.jobs_task.append(t)
                self.opt.append(good)
                self.opt_edge.append(goode)
            else:
                self.unassigned.add(t.id)
        self.n_jobs = len(self.jobs_task)
        self._geometry()

    def _geometry(self) -> None:
        """Per-job midpoints (metres), headings, street names, job-to-job distances (metres) and the
        metre->cost conversion `kappa` used by route-quality penalties."""
        g = self.graph
        n = self.n_jobs
        ws = [b[0] / max(b[2], 1e-9) for b in self.best.values() if b[2] > 0]
        self.kappa = float(np.median(ws)) if ws else 1e-4
        if n:
            lat0 = float(np.mean([g.nodes[nd]["y"] for nd in self.rnodes]))
        else:
            lat0 = 53.0
        kx = 111320.0 * math.cos(math.radians(lat0))
        ky = 110574.0
        self.kx, self.ky = kx, ky
        self.pts = np.zeros((n, 2))
        self.hd: list[list[tuple[float, float]]] = []
        self.names: list[list[str]] = []
        for j in range(n):
            hds, nms = [], []
            for o, (eid, u, v) in enumerate(self.opt_edge[j]):
                du, dv = g.nodes[u], g.nodes[v]
                dx, dy = (dv["x"] - du["x"]) * kx, (dv["y"] - du["y"]) * ky
                nrm = math.hypot(dx, dy) or 1.0
                hds.append((dx / nrm, dy / nrm))
                try:
                    nm = (g[u][v][parse_edge_id(eid)[2]].get("name") or "").strip()
                except Exception:  # noqa: BLE001
                    nm = ""
                nms.append(nm)
                if o == 0:
                    self.pts[j] = ((du["x"] + dv["x"]) / 2 * kx, (du["y"] + dv["y"]) / 2 * ky)
            self.hd.append(hds)
            self.names.append(nms)
        if n:
            U = np.array([self.opt[j][0][0] for j in range(n)])
            V = np.array([self.opt[j][0][1] for j in range(n)])
            Ls = np.minimum(self.Ln, self.Ln.T)
            D = np.minimum.reduce([Ls[np.ix_(U, U)], Ls[np.ix_(U, V)], Ls[np.ix_(V, U)], Ls[np.ix_(V, V)]])
            np.fill_diagonal(D, np.inf)
            self.Dj = D
        else:
            self.Dj = np.zeros((0, 0))

    # ---- all-pairs among relevant nodes
    def _all_pairs(self) -> None:
        N = self.N
        src = np.array([self.nid[n] for n in self.rnodes], dtype=np.int64)
        dist, pred = dijkstra(self.csr, directed=True, indices=src, return_predecessors=True)
        self.pred = pred.astype(np.int64)
        ar = np.arange(N, dtype=np.int64)[None, :]
        valid = pred >= 0
        par = np.where(valid, pred, ar).astype(np.int64)
        q = par * N + ar
        pos = np.clip(np.searchsorted(self._codes, q), 0, len(self._codes) - 1)
        match = valid & (self._codes[pos] == q)
        vals = [np.where(match, arr[pos], 0.0) for arr in (self._tv, self._lv, self._fv)]
        anc = par
        while True:
            nxt = np.take_along_axis(anc, anc, axis=1)
            if (nxt == anc).all():
                break
            vals = [v + np.take_along_axis(v, anc, axis=1) for v in vals]
            anc = nxt
        inf = ~np.isfinite(dist)

        def fin(m):
            m = m[:, src].copy()
            m[inf[:, src]] = BIG
            return m

        self.Cn = fin(np.where(inf, BIG, dist))
        self.Tn, self.Ln, self.Fn = fin(vals[0]), fin(vals[1]), fin(vals[2])
        self.Cm, self.Tm, self.Lm, self.Fm = (m.tolist() for m in (self.Cn, self.Tn, self.Ln, self.Fn))

    # ---- path expansion
    def path_edges(self, from_node: int, to_node: int) -> list[tuple]:
        """[(edge_id, u, v, length, slope)] along the shortest path."""
        if from_node == to_node:
            return []
        row = self.pred[self.rid[from_node]]
        cur = self.nid[to_node]
        s = self.nid[from_node]
        out = []
        while cur != s:
            p = int(row[cur])
            if p < 0:
                raise nx.NetworkXNoPath(f"{from_node}->{to_node}")
            _, _, ln, _, eid, sl = self.best[(p, cur)]
            out.append((eid, self.nodes[p], self.nodes[cur], ln, sl))
            cur = p
        out.reverse()
        return out

    def build_routes(self, assign: list[list[tuple[int, int]]]) -> dict[str, list[RouteStep]]:
        routes: dict[str, list[RouteStep]] = {}
        for vi, veh in enumerate(self.plows):
            steps: list[RouteStep] = []
            clock = [0.0]

            def add(kind, eid, u, v, length, slope, mode, task_id=None):
                c = vehicle_edge_cost(veh, length, slope, mode)
                steps.append(RouteStep(kind=kind, edge_id=eid, task_id=task_id, from_node=u, to_node=v,
                                       length_m=length, t_start=clock[0], t_end=clock[0] + c.time_s,
                                       fuel_l=c.fuel_l))
                clock[0] += c.time_s

            rem = self.rem_step[vi]
            if rem:
                add(StepKind.deadhead.value, rem["edge_id"], rem["u"], rem["v"], rem["length"], rem["slope"],
                    "deadhead")
            cur = self.start_node[vi]
            for j, o in assign[vi]:
                eid, u, v = self.opt_edge[j][o]
                for (pe, pu, pv, ln, sl) in self.path_edges(cur, u):
                    add(StepKind.deadhead.value, pe, pu, pv, ln, sl, "deadhead")
                d = self.graph[u][v][parse_edge_id(eid)[2]]
                add(StepKind.service.value, eid, u, v, d["length"], d.get("slope_pct", 0.0), "service",
                    self.jobs_task[j].id)
                cur = v
            for (pe, pu, pv, ln, sl) in self.path_edges(cur, self.depot_node[vi]):
                add(StepKind.return_.value, pe, pu, pv, ln, sl, "deadhead")
            routes[veh.id] = steps
        return routes

    # ---- evaluation (reference vehicle, matrices in R space)
    def eval_route(self, vi: int, items: list[tuple[int, int]]) -> tuple[float, float, float, float, float, float]:
        """(time_s, deadhead_m, fuel_l, turn_penalty_m, sum_hop_km2, max_hop_m). Hops = deadhead between
        consecutive services (start and final return hops excluded from the hop terms)."""
        Tm, Lm, Fm = self.Tm, self.Lm, self.Fm
        cur = self.s[vi]
        t, d, f = self.t0[vi], self.d0[vi], self.f0[vi]
        opt, hd = self.opt, self.hd
        pen = hop2 = maxhop = 0.0
        pj = po = -1
        for j, o in items:
            op = opt[j][o]
            a = op[0]
            hop = Lm[cur][a]
            t += Tm[cur][a] + op[2]
            d += hop
            f += Fm[cur][a] + op[3]
            if pj >= 0:
                if hop > maxhop:
                    maxhop = hop
                hop2 += (hop * 0.001) ** 2
                if hop <= 40.0:
                    h0, h1 = hd[pj][po], hd[j][o]
                    dot = h0[0] * h1[0] + h0[1] * h1[1]
                    if dot < -0.94:
                        pen += UTURN_M
                    elif dot < -0.3:
                        pen += SHARP_M * (-dot)
            cur = op[1]
            pj, po = j, o
        dep = self.dep[vi]
        return t + Tm[cur][dep], d + Lm[cur][dep], f + Fm[cur][dep], pen, hop2, maxhop

    def make_plan(self, assign, solver: str, plan_id: str, weights: ObjectiveWeights,
                  extra_unassigned: set[str] | None = None) -> Plan:
        routes = self.build_routes(assign)
        placed = {self.jobs_task[j].id for items in assign for j, _ in items}
        un = set(self.unassigned) | (extra_unassigned or set())
        un_ids = sorted(x for x in un if x not in placed)
        plan = Plan(id=plan_id, solver=solver, weights=weights, routes=routes, unassigned_task_ids=un_ids)
        return fill_plan_metrics(plan)


# ----------------------------------------------------------------------------- districts
def _repair_connectivity(P: _Problem, assign: np.ndarray, k: int, wts: np.ndarray) -> np.ndarray:
    """Make every district connected (jobs closer than ADJ_M are adjacent): non-main components are
    reassigned to the district of their nearest foreign job."""
    n = len(assign)
    D = P.Dj
    for _ in range(6):
        changed = False
        for c in range(k):
            idx = np.where(assign == c)[0]
            if len(idx) < 2:
                continue
            sub = csr_matrix(D[np.ix_(idx, idx)] <= ADJ_M)
            nc, lab = connected_components(sub, directed=False)
            if nc <= 1:
                continue
            comp_w = [float(wts[idx[lab == q]].sum()) for q in range(nc)]
            main = int(np.argmax(comp_w))
            for q in range(nc):
                if q == main:
                    continue
                members = idx[lab == q]
                foreign = np.where(assign != c)[0]
                if len(foreign) == 0:
                    continue
                dm = D[np.ix_(members, foreign)]
                flat = int(np.argmin(dm))
                tgt = int(assign[foreign[flat % len(foreign)]])
                assign[members] = tgt
                changed = True
        if not changed:
            break
    return assign


def _cluster(P: _Problem, k: int) -> list[list[int]]:
    """Compact, workload-balanced, connected districts (one list of job ids per vehicle)."""
    n = P.n_jobs
    if n == 0:
        return [[] for _ in range(k)]
    pts = P.pts
    wts = np.array([P.opt[j][0][2] for j in range(n)])
    k_eff = min(k, n)
    assign = np.zeros(n, dtype=np.int64)
    if k_eff > 1:
        mean = (pts * wts[:, None]).sum(0) / wts.sum()
        seeds = [int(np.argmax(((pts - mean) ** 2).sum(1)))]
        dmin = ((pts - pts[seeds[0]]) ** 2).sum(1)
        for _ in range(1, k_eff):
            nxt = int(np.argmax(dmin))
            seeds.append(nxt)
            dmin = np.minimum(dmin, ((pts - pts[nxt]) ** 2).sum(1))
        cent = pts[seeds].copy()

        def sq(cn):
            return ((pts[:, None, :] - cn[None, :, :]) ** 2).sum(-1)

        for _ in range(10):                      # plain weighted Lloyd
            asg = np.argmin(sq(cent), axis=1)
            for c in range(k_eff):
                m = asg == c
                if m.any():
                    cent[c] = (pts[m] * wts[m, None]).sum(0) / wts[m].sum()
        # power-diagram balancing: additive per-cluster bias keeps cells convex (hence compact)
        span = pts.max(0) - pts.min(0)
        scale = max(float(span[0] * span[1]) / k_eff, 1.0)
        target = wts.sum() / k_eff
        bias = np.zeros(k_eff)
        best_asg, best_err = None, float("inf")
        for it in range(90):
            D = sq(cent)
            asg = np.argmin(D - bias[None, :], axis=1)
            load = np.bincount(asg, weights=wts, minlength=k_eff)
            err = (load - target) / target
            e = float(np.abs(err).max())
            if e < best_err:
                best_err, best_asg = e, asg.copy()
            if e < 0.05:
                break
            bias -= 0.3 * scale * err
            if it < 45 and it % 5 == 4:
                for c in range(k_eff):
                    m = asg == c
                    if m.any():
                        cent[c] = (pts[m] * wts[m, None]).sum(0) / wts[m].sum()
        assign = best_asg
        assign = _repair_connectivity(P, assign, k_eff, wts)
    clusters = [[int(j) for j in np.where(assign == c)[0]] for c in range(k_eff)]
    cost = np.zeros((k, k_eff))
    for vi in range(k):
        s_node = P.graph.nodes[P.start_node[vi]]
        e_node = P.graph.nodes[P.rnodes[P.dep[vi]]]
        for c in range(k_eff):
            if clusters[c]:
                cm = P.pts[clusters[c]].mean(0)
                cost[vi, c] = _node_dist(P, s_node, cm) + _node_dist(P, e_node, cm)
    ri, ci = linear_sum_assignment(cost)
    out: list[list[int]] = [[] for _ in range(k)]
    for r, c in zip(ri, ci):
        out[r] = clusters[c]
    return out


def _node_dist(P: _Problem, node: dict, xy) -> float:
    return math.hypot(node["x"] * P.kx - xy[0], node["y"] * P.ky - xy[1])


# ----------------------------------------------------------------------------- construction
def _path_scan(P: _Problem, vi: int, cluster: list[int], rule: int, rng: random.Random,
               noise: float) -> list[tuple[int, int]]:
    """Greedy route through a district: nearest unserved task first (convex penalty on long hops),
    then re-ranked among the best few by street continuation / heading / U-turn avoidance."""
    Cm, Lm = P.Cm, P.Lm
    kap = P.kappa
    dep = P.dep[vi]
    cur = P.s[vi]
    rem = list(cluster)
    items: list[tuple[int, int]] = []
    total_w = sum(P.opt[j][0][2] for j in cluster) or 1.0
    done_w = 0.0
    prev: tuple[int, int] | None = None
    while rem:
        cands = []
        row, lrow = Cm[cur], Lm[cur]
        for j in rem:
            for o, op in enumerate(P.opt[j]):
                c = row[op[0]]
                if c >= BIG:
                    continue
                hop = lrow[op[0]]
                c += op[5] + kap * hop * hop / 800.0
                if noise:
                    c *= 1.0 + noise * rng.random()
                cands.append((c, j, o))
        if not cands:
            break
        top = heapq.nsmallest(10, cands)
        ref = []
        for c, j, o in top:
            if prev is not None and lrow[P.opt[j][o][0]] <= 60.0:
                h0, h1 = P.hd[prev[0]][prev[1]], P.hd[j][o]
                dot = h0[0] * h1[0] + h0[1] * h1[1]
                if dot < -0.94:
                    c += kap * UTURN_M
                elif dot < -0.3:
                    c += kap * SHARP_M * (-dot)
                elif dot > 0.5:
                    nm = P.names[j][o]
                    c -= kap * (40.0 if nm and nm == P.names[prev[0]][prev[1]] else 15.0) * dot
            ref.append((round(c, 9), j, o))
        best = min(ref)
        pick = best
        if rule != 0:
            lim = best[0] + abs(best[0]) * 0.08 + 1e-9
            pool = [x for x in ref if x[0] <= lim]
            r = rule
            if r == 5:
                r = 1 if done_w < total_w / 2 else 2
            if r == 1:
                pick = max(pool, key=lambda x: (Cm[P.opt[x[1]][x[2]][1]][dep], -x[0], -x[1], -x[2]))
            elif r == 2:
                pick = min(pool, key=lambda x: (Cm[P.opt[x[1]][x[2]][1]][dep], x[0], x[1], x[2]))
            elif r == 3:
                pick = max(pool, key=lambda x: (P.opt[x[1]][x[2]][2] / max(P.opt[x[1]][x[2]][4], 1e-9), -x[0], -x[1]))
            elif r == 4:
                pick = min(pool, key=lambda x: (P.opt[x[1]][x[2]][2] / max(P.opt[x[1]][x[2]][4], 1e-9), x[0], x[1]))
        _, j, o = pick
        items.append((j, o))
        rem.remove(j)
        done_w += P.opt[j][0][2]
        cur = P.opt[j][o][1]
        prev = (j, o)
    return items


def _extra(m, wd_scale: float = 1.0) -> float:
    """Route-quality penalty in deadhead-km equivalents from an eval_route tuple."""
    if len(m) < 6:
        return 0.0
    return m[3] / 1000.0 + HOP2_W * m[4] + HOP_EXC_W * max(0.0, m[5] - HOP_MAX_M) / 1000.0


def _scalar(P: _Problem, m: tuple) -> float:
    d0, t0_, f0_ = DEFAULT_SCALES
    w = P.w
    return (w.deadhead * (m[1] / 1000.0 + _extra(m)) / d0 + w.time * m[0] / 60.0 / t0_
            + w.fuel * m[2] / f0_)


# ----------------------------------------------------------------------------- local search
class _LS:
    def __init__(self, P: _Problem, routes: list[list[tuple[int, int]]], rng: random.Random) -> None:
        self.P, self.rng = P, rng
        self.routes = [list(r) for r in routes]
        self.nv = len(routes)
        self.mets = [P.eval_route(v, self.routes[v]) for v in range(self.nv)]
        w = P.w
        self.wd, self.wt, self.wf = w.deadhead, w.time, w.fuel
        self.D0 = max(sum(m[1] for m in self.mets) / 1000.0, 1e-6)
        self.T0 = max(max(m[0] for m in self.mets) / 60.0, 1e-6)
        self.F0 = max(sum(m[2] for m in self.mets), 1e-6)
        self.obj = self._obj(self.mets)
        n = P.n_jobs
        self.neigh: list[list[int]] = [[] for _ in range(n)]
        if n > 1:
            U = np.array([P.opt[j][0][0] for j in range(n)])
            V = np.array([P.opt[j][0][1] for j in range(n)])
            Cs = np.minimum(P.Cn, P.Cn.T)
            D = np.minimum.reduce([Cs[np.ix_(U, U)], Cs[np.ix_(U, V)], Cs[np.ix_(V, U)], Cs[np.ix_(V, V)]])
            np.fill_diagonal(D, np.inf)
            K = min(10, n - 1)
            idx = np.argsort(D, axis=1, kind="stable")[:, :K]
            self.neigh = [[int(x) for x in row] for row in idx]
        self.rev: list[list[int | None]] = []
        for j in range(n):
            r = []
            for o, op in enumerate(P.opt[j]):
                alt = None
                for o2, op2 in enumerate(P.opt[j]):
                    if o2 != o and op2[0] == op[1] and op2[1] == op[0]:
                        alt = o2
                        break
                r.append(alt)
            self.rev.append(r)
        self._where()
        # static district centroids (metres) for the compactness test on inter-district moves
        self.cent: list[np.ndarray | None] = [
            (P.pts[[j for j, _ in r]].mean(0) if r else None) for r in self.routes]

    def _compact_ok(self, j: int, k: int, r1: int, r2: int) -> bool:
        """May job j (now in district r1) move next to job k in district r2? Only if it is adjacent to k
        and not much farther from r2's centroid than from r1's."""
        if r1 == r2:
            return True
        P = self.P
        if P.Dj[j][k] > NEAR_M:
            return False
        c1, c2 = self.cent[r1], self.cent[r2]
        if c1 is None or c2 is None:
            return True
        p = P.pts[j]
        d1 = math.hypot(p[0] - c1[0], p[1] - c1[1])
        d2 = math.hypot(p[0] - c2[0], p[1] - c2[1])
        return d2 <= 1.3 * d1 + 400.0

    def _obj(self, mets) -> float:
        return (self.wd * (sum(m[1] / 1000.0 + _extra(m) for m in mets)) / self.D0
                + self.wt * max(m[0] for m in mets) / 60.0 / self.T0
                + self.wf * sum(m[2] for m in mets) / self.F0)

    def _where(self) -> None:
        self.where = {}
        for r, items in enumerate(self.routes):
            for i, (j, _) in enumerate(items):
                self.where[j] = (r, i)

    def _score(self, changes: dict[int, list]) -> tuple[float, dict[int, tuple]]:
        new = {r: self.P.eval_route(r, it) for r, it in changes.items()}
        mets = [new.get(r, self.mets[r]) for r in range(self.nv)]
        return self._obj(mets), new

    def _best_of(self, cands: list[dict[int, list]]):
        best = None
        for ch in cands:
            o, new = self._score(ch)
            if best is None or o < best[0]:
                best = (o, ch, new)
        return best

    def _accept(self, best) -> bool:
        if best is None or best[0] >= self.obj - 1e-10:
            return False
        o, ch, new = best
        for r, it in ch.items():
            self.routes[r] = it
            self.mets[r] = new[r]
        self.obj = o
        self._where()
        return True

    def run(self, deadline: float, stall_limit: int, max_iters: int) -> int:
        rng = self.rng
        n = self.P.n_jobs
        if n == 0 or self.nv == 0:
            return 0
        stall = 0
        it = 0
        while it < max_iters and stall < stall_limit:
            it += 1
            if (it & 63) == 0 and time.perf_counter() > deadline:
                break
            if rng.random() < 0.4:
                lr = max(range(self.nv), key=lambda r: self.mets[r][0])
                j = rng.choice(self.routes[lr])[0] if self.routes[lr] else rng.randrange(n)
            else:
                j = rng.randrange(n)
            r1, i1 = self.where[j]
            mt = rng.random()
            improved = False
            nb = self.neigh[j]
            k = rng.choice(nb) if nb else None
            if mt < 0.40 and k is not None:
                improved = self._relocate(j, r1, i1, k)
            elif mt < 0.50:
                improved = self._flip(j, r1, i1)
            elif mt < 0.62 and k is not None:
                improved = self._swap(j, r1, i1, k)
            elif mt < 0.78 and k is not None:
                improved = self._two_opt(j, r1, i1, k)
            elif k is not None:
                improved = self._or_opt(j, r1, i1, k)
            stall = 0 if improved else stall + 1
        return it

    def _relocate(self, j, r1, i1, k) -> bool:
        r2, i2 = self.where[k]
        if not self._compact_ok(j, k, r1, r2):
            return False
        base1 = self.routes[r1][:i1] + self.routes[r1][i1 + 1:]
        cands = []
        for after in (0, 1):
            for o in range(len(self.P.opt[j])):
                if r1 == r2:
                    pos = i2 + after - (1 if i2 > i1 else 0)
                    cands.append({r1: base1[:pos] + [(j, o)] + base1[pos:]})
                else:
                    tgt = self.routes[r2]
                    pos = i2 + after
                    cands.append({r1: base1, r2: tgt[:pos] + [(j, o)] + tgt[pos:]})
        return self._accept(self._best_of(cands))

    def _flip(self, j, r1, i1) -> bool:
        cur_o = self.routes[r1][i1][1]
        cands = []
        for o in range(len(self.P.opt[j])):
            if o != cur_o:
                new = list(self.routes[r1])
                new[i1] = (j, o)
                cands.append({r1: new})
        return self._accept(self._best_of(cands)) if cands else False

    def _swap(self, j, r1, i1, k) -> bool:
        r2, i2 = self.where[k]
        if r1 == r2 and i1 == i2:
            return False
        if r1 != r2 and not (self._compact_ok(j, k, r1, r2) and self._compact_ok(k, j, r2, r1)):
            return False
        cands = []
        for oj in range(len(self.P.opt[j])):
            for ok in range(len(self.P.opt[k])):
                if r1 == r2:
                    new = list(self.routes[r1])
                    new[i1], new[i2] = (k, ok), (j, oj)
                    cands.append({r1: new})
                else:
                    a, b = list(self.routes[r1]), list(self.routes[r2])
                    a[i1], b[i2] = (k, ok), (j, oj)
                    cands.append({r1: a, r2: b})
        return self._accept(self._best_of(cands))

    def _two_opt(self, j, r1, i1, k) -> bool:
        r2, i2 = self.where[k]
        if r1 != r2:
            return False      # tail exchange between districts would destroy compactness
        lo, hi = (i1, i2) if i1 < i2 else (i2, i1)
        if lo == hi:
            return False
        r = self.routes[r1]
        seg = []
        for (jj, oo) in reversed(r[lo:hi + 1]):
            alt = self.rev[jj][oo]
            seg.append((jj, alt if alt is not None else oo))
        return self._accept(self._best_of([{r1: r[:lo] + seg + r[hi + 1:]}]))

    def _or_opt(self, j, r1, i1, k) -> bool:
        L = self.rng.choice((2, 3))
        r = self.routes[r1]
        seg = r[i1:i1 + L]
        if len(seg) < 2 or any(k == s[0] for s in seg):
            return False
        base = r[:i1] + r[i1 + L:]
        r2, i2 = self.where[k]
        if r1 != r2 and not all(self._compact_ok(jj, k, r1, r2) for jj, _ in seg):
            return False
        rseg = []
        for (jj, oo) in reversed(seg):
            alt = self.rev[jj][oo]
            rseg.append((jj, alt if alt is not None else oo))
        cands = []
        for after in (0, 1):
            for sg in (seg, rseg):
                if r1 == r2:
                    pos = i2 + after - (L if i2 > i1 else 0)
                    cands.append({r1: base[:pos] + sg + base[pos:]})
                else:
                    tgt = self.routes[r2]
                    pos = i2 + after
                    cands.append({r1: base, r2: tgt[:pos] + sg + tgt[pos:]})
        return self._accept(self._best_of(cands))


# ----------------------------------------------------------------------------- public API
def solve_plows(graph: nx.MultiDiGraph, tasks: list[Task], vehicles: list[Vehicle],
                weights: ObjectiveWeights | None = None,
                start_positions: dict[str, Position] | None = None,
                time_limit_s: float = 5.0, seed: int = 0) -> Plan:
    """Optimise plow routes (Capacitated-Arc-Routing style, minimise solver.cost.objective).

    * Only tasks with status in (pending, assigned) and passes_done < passes are planned;
      each needs (passes - passes_done) service steps, in either direction (any edge in
      task.edge_ids). Already-done tasks are ignored (replanning).
    * Only vehicles with kind=plow and status != broken are used.
    * start_positions: vehicle_id -> Position (mid-edge allowed: the remainder of the current
      edge is emitted first as a deadhead step with length_m = remaining length, then the route
      continues from the edge's end node). None => start at (vehicle.start_node or depot_node).
    * Every route ends with kind=return steps to cost.finish_node (dump by default, else depot_node).
    * Steps are edge-level, contiguous, t_start/t_end/fuel_l via solver.cost from t=0.
    * `seed` (extra, backward-compatible kwarg) makes the randomised parts reproducible.
    * Tasks that cannot be reached go to plan.unassigned_task_ids (never raises for that).
    """
    t_begin = time.perf_counter()
    weights = weights or ObjectiveWeights()
    plows = _active_plows(vehicles)
    if not plows:
        ids = sorted({t.id for t in tasks if t.status in _ACTIVE and t.passes_done < t.passes})
        return Plan(id="P-0001", solver="carp", weights=weights, routes={}, unassigned_task_ids=ids)
    rng = random.Random(seed)
    P = _Problem(graph, tasks, plows, weights, start_positions)
    nv = len(plows)
    deadline = t_begin + max(0.2, time_limit_s * 0.85)

    clusters = _cluster(P, nv)
    build_deadline = t_begin + max(0.1, time_limit_s * 0.2)
    routes: list[list[tuple[int, int]]] = []
    for vi in range(nv):
        cl = clusters[vi]
        best_items, best_val = None, float("inf")
        trials = [(r, 0.0) for r in range(6)] + [(1 + (x % 5), 0.05 + 0.04 * (x % 6)) for x in range(18)]
        for ti, (rule, noise) in enumerate(trials):
            if ti >= 3 and time.perf_counter() > build_deadline:
                break
            items = _path_scan(P, vi, cl, rule, rng, noise)
            val = _scalar(P, P.eval_route(vi, items)) + (1e6 if len(items) < len(cl) else 0.0)
            if val < best_val:
                best_items, best_val = items, val
        routes.append(best_items or [])
    placed = {j for r in routes for j, _ in r}
    leftover = {P.jobs_task[j].id for j in range(P.n_jobs) if j not in placed}

    if P.n_jobs and not leftover:
        ls = _LS(P, routes, rng)
        ls.run(deadline, stall_limit=max(3000, 40 * P.n_jobs), max_iters=2_000_000)
        routes = ls.routes
    return P.make_plan(routes, "carp", "P-0001", weights, leftover)
