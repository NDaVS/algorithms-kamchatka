"""Synthetic test city (IMPLEMENTED). Returns the SAME structures as the real loader.

    from tests.synthetic import build_synthetic_city
    city = build_synthetic_city(seed=42)
    city.graph    # nx.MultiDiGraph in canonical format (core.graph docstring)
    city.tasks    # list[Task] from core.graph.build_tasks (edge attr task_id set)
    city.vehicles # list[Vehicle] (default fleet at depot)
    city.depot_node, city.dump_node

Layout: 12 rows x 13 cols = 156 nodes, ids r*13+c, node 0 = depot "База №1" (SW corner),
node 155 = dump "Снегоотвал №1" (NE corner). Gaussian hill (~80 m) in the middle, mild tilt,
seeded noise. Rows r%3==1 are one-way (direction alternates), all columns two-way, rows 0 and
11 two-way => strongly connected. Row 0 horizontal edges are the depot yard: highway=service,
required=False. Columns 0/6/12 are "primary".
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

import networkx as nx

from core.elevation import slope_pct
from core.graph import BBOX, build_tasks, graph_from_models, haversine_m, make_edge_id
from core.models import Edge, Node, Task, Vehicle, make_default_fleet

ROW_NAMES = ["Промышленная", "Ленинская", "Океанская", "Советская", "Владивостокская", "Мишенная",
             "Партизанская", "Кроноцкая", "Рябиковская", "Пограничная", "Тушканова", "Зелёная"]
COL_NAMES = ["Командорская", "Лесная", "Озерновская", "Крашенинникова", "Сахалинская", "Ключевская",
             "Карла Маркса", "Чубарова", "Ломоносова", "Горького", "Вилюйская", "Пушкинская", "Беринга"]


@dataclass
class SyntheticCity:
    graph: nx.MultiDiGraph
    nodes: list[Node]
    edges: list[Edge]
    tasks: list[Task]
    vehicles: list[Vehicle]
    depot_node: int
    dump_node: int
    bbox: tuple[float, float, float, float]


def build_synthetic_city(seed: int = 42, rows: int = 12, cols: int = 13,
                         n_plows: int = 4, n_trucks: int = 0) -> SyntheticCity:
    rng = random.Random(seed)
    lon0, lat0, lon1, lat1 = BBOX
    pad = 0.004
    dx = (lon1 - lon0 - 2 * pad) / (cols - 1)
    dy = (lat1 - lat0 - 2 * pad) / (rows - 1)
    cr, cc = (rows - 1) / 2, (cols - 1) / 2
    depot, dump = 0, rows * cols - 1

    nodes: list[Node] = []
    for r in range(rows):
        for c in range(cols):
            nid = r * cols + c
            lon = lon0 + pad + c * dx + rng.uniform(-0.1, 0.1) * dx
            lat = lat0 + pad + r * dy + rng.uniform(-0.1, 0.1) * dy
            d2 = ((r - cr) / (rows / 3)) ** 2 + ((c - cc) / (cols / 3)) ** 2
            z = 80 * math.exp(-d2) + 1.2 * c + rng.uniform(-1.5, 1.5) + 3
            kind, name = "junction", None
            if nid == depot:
                kind, name = "depot", "База №1"
            elif nid == dump:
                kind, name = "dump", "Снегоотвал №1"
            nodes.append(Node(id=nid, lon=round(lon, 6), lat=round(lat, 6), elevation_m=round(z, 1),
                              kind=kind, name=name))
    nb = {n.id: n for n in nodes}

    edges: list[Edge] = []

    def add(u: int, v: int, name: str, highway: str, oneway: bool, required: bool, sid: str) -> None:
        a, b = nb[u], nb[v]
        length = haversine_m(a.lon, a.lat, b.lon, b.lat)
        geom = [[a.lon, a.lat], [b.lon, b.lat]]
        if rng.random() < 0.3:  # slight bend
            mx, my = (a.lon + b.lon) / 2, (a.lat + b.lat) / 2
            geom = [geom[0], [mx + rng.uniform(-1, 1) * 0.0003, my + rng.uniform(-1, 1) * 0.0002], geom[1]]
        s = slope_pct(a.elevation_m, b.elevation_m, length)
        fid, rid = make_edge_id(u, v, 0), make_edge_id(v, u, 0)
        edges.append(Edge(id=fid, u=u, v=v, length_m=round(length, 1), slope_pct=round(s, 2), geometry=geom,
                          name=name, highway=highway, oneway=oneway, required=required, street_id=sid,
                          reverse_edge_id=None if oneway else rid))
        if not oneway:
            edges.append(Edge(id=rid, u=v, v=u, length_m=round(length, 1), slope_pct=round(-s, 2),
                              geometry=[p for p in reversed(geom)], name=name, highway=highway, oneway=False,
                              required=required, street_id=sid, reverse_edge_id=fid))

    for r in range(rows):
        row_oneway = (r % 3 == 1)
        east = ((r // 3) % 2 == 0)
        for c in range(cols - 1):
            u, v = r * cols + c, r * cols + c + 1
            if row_oneway and not east:
                u, v = v, u
            yard = (r == 0)
            add(u, v, f"улица {ROW_NAMES[r % len(ROW_NAMES)]}", "service" if yard else "residential",
                row_oneway, not yard, f"S-{min(u, v)}-{max(u, v)}")
    for c in range(cols):
        for r in range(rows - 1):
            u, v = r * cols + c, (r + 1) * cols + c
            add(u, v, f"проспект {COL_NAMES[c % len(COL_NAMES)]}", "primary" if c in (0, cols // 2, cols - 1)
                else "residential", False, True, f"S-{u}-{v}")

    G = graph_from_models(nodes, edges, {"bbox": list(BBOX), "depot_node": depot, "dump_node": dump})
    tasks = build_tasks(G)
    # refresh edge models so task_id is included
    from core.graph import graph_to_models
    nodes, edges = graph_to_models(G)
    vehicles = make_default_fleet(depot, n_plows, n_trucks)
    return SyntheticCity(G, nodes, edges, tasks, vehicles, depot, dump, BBOX)
