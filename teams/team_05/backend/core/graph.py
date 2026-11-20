"""Graph structures and loading. See CONTRACTS.md section 2.

networkx.MultiDiGraph conventions
---------------------------------
graph attrs : G.graph["bbox"]=[lon_min,lat_min,lon_max,lat_max], ["depot_node"]=int, ["dump_node"]=int,
              ["crs"]="EPSG:4326"
node attrs  : x (lon), y (lat)   [osmnx naming], elevation (m), kind ("junction"|"depot"|"dump"), name
edge attrs  : edge_id (str "<u>-<v>-<key>"), length (m), slope_pct, geometry (list [[lon,lat],..],
              NOT shapely), name, highway, oneway (bool), required (bool), street_id, reverse_edge_id,
              task_id (set by build_tasks)
Edge key is the multigraph key (int). Edge lookup: G[u][v][key].
"""
from __future__ import annotations

import json
import math
import weakref
from pathlib import Path

import networkx as nx

from core.models import Edge, Node, Position, Task

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
GRAPH_PATH = DATA_DIR / "graph.json"
BASEMAP_PATH = DATA_DIR / "basemap.geojson"
BBOX = (158.60, 53.00, 158.68, 53.07)  # lon_min, lat_min, lon_max, lat_max


# ----------------------------------------------------------------- implemented helpers
def make_edge_id(u: int, v: int, key: int = 0) -> str:
    return f"{u}-{v}-{key}"


def parse_edge_id(edge_id: str) -> tuple[int, int, int]:
    u, v, k = edge_id.split("-")
    return int(u), int(v), int(k)


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def graph_from_models(nodes: list[Node], edges: list[Edge], meta: dict | None = None) -> nx.MultiDiGraph:
    """Build the canonical MultiDiGraph from model lists (used by the loader and synthetic)."""
    G = nx.MultiDiGraph(**(meta or {}))
    G.graph.setdefault("crs", "EPSG:4326")
    for n in nodes:
        G.add_node(n.id, x=n.lon, y=n.lat, elevation=n.elevation_m, kind=n.kind, name=n.name)
    for e in edges:
        G.add_edge(e.u, e.v, key=e.key, edge_id=e.id, length=e.length_m, slope_pct=e.slope_pct,
                   geometry=e.geometry, name=e.name, highway=e.highway, oneway=e.oneway,
                   required=e.required, street_id=e.street_id, reverse_edge_id=e.reverse_edge_id,
                   task_id=e.task_id)
    return G


def graph_to_models(G: nx.MultiDiGraph) -> tuple[list[Node], list[Edge]]:
    nodes = [Node(id=n, lon=d["x"], lat=d["y"], elevation_m=d.get("elevation", 0.0),
                  kind=d.get("kind", "junction"), name=d.get("name")) for n, d in G.nodes(data=True)]
    edges = [edge_model(G, u, v, k) for u, v, k in G.edges(keys=True)]
    return nodes, edges


def edge_model(G: nx.MultiDiGraph, u: int, v: int, k: int = 0) -> Edge:
    d = G[u][v][k]
    return Edge(id=d["edge_id"], u=u, v=v, key=k, length_m=d["length"], slope_pct=d.get("slope_pct", 0.0),
                geometry=d["geometry"], name=d.get("name"), highway=d.get("highway", "residential"),
                oneway=d.get("oneway", False), required=d.get("required", True),
                street_id=d.get("street_id"), reverse_edge_id=d.get("reverse_edge_id"),
                task_id=d.get("task_id"))


def edge_data(G: nx.MultiDiGraph, edge_id: str) -> dict:
    u, v, k = parse_edge_id(edge_id)
    return G[u][v][k]


def save_graph(G: nx.MultiDiGraph, path: Path = GRAPH_PATH) -> None:
    """Write data/graph.json: {"meta":{...graph attrs}, "nodes":[Node...], "edges":[Edge...]}."""
    nodes, edges = graph_to_models(G)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"meta": dict(G.graph), "nodes": [n.model_dump() for n in nodes],
                                "edges": [e.model_dump() for e in edges]}, ensure_ascii=False), encoding="utf-8")


def load_graph(path: Path = GRAPH_PATH) -> nx.MultiDiGraph:
    """Read data/graph.json (utf-8) into the canonical MultiDiGraph. If the file is missing the
    caller (app startup) falls back to tests.synthetic.build_synthetic_city()."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return graph_from_models([Node(**n) for n in raw["nodes"]], [Edge(**e) for e in raw["edges"]], raw.get("meta"))


def build_tasks(G: nx.MultiDiGraph, passes: int = 1) -> list[Task]:
    """One Task per physical street segment with required=True. Groups directed edges by
    `street_id` (fallback: pair via reverse_edge_id; or single edge). Sets edge attr `task_id`.
    Task ids "T-0001".. assigned in sorted edge_id order (deterministic). Mutates G."""
    seen: dict[str, list[str]] = {}
    for u, v, k, d in sorted(G.edges(keys=True, data=True), key=lambda x: x[3]["edge_id"]):
        if not d.get("required", True):
            continue
        sid = d.get("street_id") or "|".join(sorted([d["edge_id"], d.get("reverse_edge_id") or d["edge_id"]]))
        seen.setdefault(sid, []).append(d["edge_id"])
    tasks: list[Task] = []
    for i, (sid, eids) in enumerate(sorted(seen.items()), start=1):
        first = edge_data(G, eids[0])
        u, v, _ = parse_edge_id(eids[0])
        t = Task(id=f"T-{i:04d}", edge_ids=eids, u=u, v=v, street_name=first.get("name"),
                 length_m=first["length"], passes=passes)
        for eid in eids:
            edge_data(G, eid)["task_id"] = t.id
        tasks.append(t)
    return tasks


# ----------------------------------------------------------------- data/graph agent implementations
NOT_REQUIRED_HIGHWAYS = {"service", "track", "motorway", "motorway_link", "trunk_link", "busway", "path"}
_FALLBACK_DEPOT = (158.612, 53.010)
_FALLBACK_DUMP = (158.670, 53.060)


def _first(v):
    """osmnx sometimes gives lists for merged edges; take the first element."""
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    return v


def _geom_coords(d: dict, a: tuple[float, float], b: tuple[float, float]) -> list[list[float]]:
    """Edge geometry as [[lon,lat],..] oriented a->b (shapely LineString, WKT, or straight line)."""
    g = d.get("geometry")
    coords: list[list[float]] | None = None
    if g is not None:
        if isinstance(g, str):
            try:
                from shapely import wkt  # lazy: only for real OSM data
                g = wkt.loads(g)
            except Exception:  # noqa: BLE001
                g = None
        if g is not None and hasattr(g, "coords"):
            coords = [[float(x), float(y)] for x, y in g.coords]
    if not coords or len(coords) < 2:
        return [[a[0], a[1]], [b[0], b[1]]]
    da = haversine_m(coords[0][0], coords[0][1], *a) + haversine_m(coords[-1][0], coords[-1][1], *b)
    db = haversine_m(coords[0][0], coords[0][1], *b) + haversine_m(coords[-1][0], coords[-1][1], *a)
    if db < da:
        coords.reverse()
    coords[0] = [a[0], a[1]]
    coords[-1] = [b[0], b[1]]
    return coords


def _poly_len(coords: list[list[float]]) -> float:
    return sum(haversine_m(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
               for i in range(len(coords) - 1))


def _download_osm(bbox):
    """osmnx drive network for bbox (works with osmnx 1.x and 2.x signatures)."""
    import osmnx as ox  # lazy import
    west, south, east, north = bbox
    try:
        return ox.graph_from_bbox(bbox=(west, south, east, north), network_type="drive", simplify=True)
    except TypeError:  # osmnx 1.x: (north, south, east, west)
        return ox.graph_from_bbox(north, south, east, west, network_type="drive", simplify=True)


def build_graph_from_osm(bbox: tuple[float, float, float, float] = BBOX, raw=None, with_elevation: bool = True,
                         depot_lonlat: tuple[float, float] | None = None,
                         dump_lonlat: tuple[float, float] | None = None) -> nx.MultiDiGraph:
    """osmnx.graph_from_bbox(drive) -> canonical graph: add elevation (core.elevation), slope_pct,
    edge_id, street_id/reverse_edge_id pairing, required flag (False for service/track/motorway),
    geometry as [[lon,lat],..]; mark depot/dump nodes (nearest to configured coordinates).
    Used by scripts/build_graph.py, which then calls save_graph().

    Extra (backward-compatible) kwargs: `raw` = an already loaded osmnx graph (e.g. from graphml; skips
    the download); `with_elevation=False` skips attach_elevation; depot/dump lon/lat override app.config.
    Nodes are relabelled 0..N-1 (sorted by OSM id); only the largest strongly connected part is kept."""
    if raw is None:
        raw = _download_osm(bbox)
    # largest strongly connected component so every task is reachable
    comps = list(nx.strongly_connected_components(raw))
    if comps:
        keep = max(comps, key=len)
        raw = raw.subgraph(keep).copy()
    order = sorted(raw.nodes)
    new_id = {old: i for i, old in enumerate(order)}

    G = nx.MultiDiGraph(bbox=list(bbox), depot_node=0, dump_node=0, crs="EPSG:4326")
    for old in order:
        d = raw.nodes[old]
        G.add_node(new_id[old], x=float(d["x"]), y=float(d["y"]), elevation=0.0, kind="junction", name=None)

    # first pass: edges with geometry, length, tags
    for u0, v0, k0, d in raw.edges(keys=True, data=True):
        u, v = new_id[u0], new_id[v0]
        a = (G.nodes[u]["x"], G.nodes[u]["y"])
        b = (G.nodes[v]["x"], G.nodes[v]["y"])
        geom = _geom_coords(d, a, b)
        length = float(d.get("length") or 0.0) or _poly_len(geom)
        hw = _first(d.get("highway")) or "residential"
        name = _first(d.get("name"))
        key = G.add_edge(u, v, key=k0 if isinstance(k0, int) else None, length=round(length, 1), geometry=geom,
                         name=name if name else None, highway=str(hw), required=str(hw) not in NOT_REQUIRED_HIGHWAYS,
                         slope_pct=0.0, oneway=True, street_id=None, reverse_edge_id=None, task_id=None)
        G[u][v][key]["edge_id"] = make_edge_id(u, v, key)

    # pairing: reverse edge = (v,u,k') with similar length and same name
    used: set[str] = set()
    for u, v, k, d in sorted(G.edges(keys=True, data=True), key=lambda x: x[3]["edge_id"]):
        if d["edge_id"] in used or u == v or not G.has_edge(v, u):
            continue
        best = None
        for k2, d2 in G[v][u].items():
            if d2["edge_id"] in used or d2["edge_id"] == d["edge_id"]:
                continue
            if abs(d2["length"] - d["length"]) <= 0.15 * max(d["length"], 1.0) and d2.get("name") == d.get("name"):
                diff = abs(d2["length"] - d["length"])
                if best is None or diff < best[0]:
                    best = (diff, k2, d2)
        if best is None:
            continue
        _, k2, d2 = best
        sid = f"S-{min(u, v)}-{max(u, v)}-{min(k, k2)}"
        d.update(street_id=sid, reverse_edge_id=d2["edge_id"], oneway=False)
        d2.update(street_id=sid, reverse_edge_id=d["edge_id"], oneway=False)
        used.update((d["edge_id"], d2["edge_id"]))
    for u, v, k, d in G.edges(keys=True, data=True):
        if d["street_id"] is None:
            d["street_id"] = f"S-{u}-{v}-{k}"
            d["oneway"] = True
        if not d["name"]:
            d["name"] = "Безымянный проезд"

    # depot / dump
    if depot_lonlat is None or dump_lonlat is None:
        try:
            from app import config as _cfg  # noqa: PLC0415 (lean module, only imports core.models)
            dl, ul = _cfg.DEPOT_LONLAT, _cfg.DUMP_LONLAT
        except Exception:  # noqa: BLE001
            dl, ul = _FALLBACK_DEPOT, _FALLBACK_DUMP
        depot_lonlat = depot_lonlat or dl
        dump_lonlat = dump_lonlat or ul

    def nearest_node(lonlat, exclude=()):
        return min((n for n in G.nodes if n not in exclude),
                   key=lambda n: haversine_m(G.nodes[n]["x"], G.nodes[n]["y"], lonlat[0], lonlat[1]))

    depot = nearest_node(depot_lonlat)
    dump = nearest_node(dump_lonlat, exclude=(depot,))
    G.nodes[depot].update(kind="depot", name="База №1")
    G.nodes[dump].update(kind="dump", name="Снегоотвал №1")
    G.graph.update(depot_node=depot, dump_node=dump)

    if with_elevation:
        from core.elevation import attach_elevation
        attach_elevation(G)
    return G


# ---- shortest paths (cached per graph / regime / source)
_SP_CACHE: "weakref.WeakKeyDictionary[nx.MultiDiGraph, dict]" = weakref.WeakKeyDictionary()


def _edge_time(d: dict, kind: str, mode: str, loaded: bool) -> float:
    from solver.cost import edge_cost
    return edge_cost(d["length"], d.get("slope_pct", 0.0), kind, mode, loaded).time_s


def shortest_path_edges(G: nx.MultiDiGraph, source: int, target: int, kind: str = "plow",
                        mode: str = "deadhead", loaded: bool = False) -> list[str]:
    """Edge-id path minimising solver.cost time (asymmetric by slope). [] if source==target;
    raises networkx.NetworkXNoPath if unreachable. Cached per (graph, regime, source); the cache is
    dropped automatically when the edge count changes."""
    if source == target:
        return []
    store = _SP_CACHE.setdefault(G, {})
    stamp = (G.number_of_nodes(), G.number_of_edges())
    if store.get("_stamp") != stamp:
        store.clear()
        store["_stamp"] = stamp
    ck = (kind, mode, bool(loaded), source)
    if ck not in store:
        def w(u, v, keydict):
            return min(_edge_time(d, kind, mode, loaded) for d in keydict.values())
        store[ck] = nx.single_source_dijkstra_path(G, source, weight=w)
    paths = store[ck]
    if target not in paths:
        raise nx.NetworkXNoPath(f"No path from {source} to {target}")
    nodes = paths[target]
    out: list[str] = []
    for a, b in zip(nodes, nodes[1:]):
        k = min(G[a][b], key=lambda kk: _edge_time(G[a][b][kk], kind, mode, loaded))
        out.append(G[a][b][k]["edge_id"])
    return out


def _interp(coords: list[list[float]], frac: float) -> tuple[float, float, float]:
    """(lon, lat, heading_deg) at fraction [0,1] of the polyline length."""
    seg = [haversine_m(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
           for i in range(len(coords) - 1)]
    total = sum(seg)
    target = max(0.0, min(1.0, frac)) * total
    acc = 0.0
    for i, sl in enumerate(seg):
        if acc + sl >= target or i == len(seg) - 1:
            t = 0.0 if sl <= 0 else min(1.0, max(0.0, (target - acc) / sl))
            (x0, y0), (x1, y1) = coords[i], coords[i + 1]
            dx = (x1 - x0) * math.cos(math.radians((y0 + y1) / 2))
            heading = (math.degrees(math.atan2(dx, y1 - y0)) + 360.0) % 360.0
            return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, heading
        acc += sl
    return coords[-1][0], coords[-1][1], 0.0


def edge_position(G: nx.MultiDiGraph, edge_id: str, offset_m: float) -> Position:
    """Interpolate lon/lat at `offset_m` along the edge geometry (clamped to [0,length])."""
    d = edge_data(G, edge_id)
    length = float(d["length"])
    off = max(0.0, min(length, offset_m))
    frac = off / length if length > 0 else 0.0
    lon, lat, hd = _interp(d["geometry"], frac)
    return Position(lon=lon, lat=lat, edge_id=edge_id, offset_m=off, heading_deg=hd)


def nearest_edge(G: nx.MultiDiGraph, lon: float, lat: float, required_only: bool = False) -> tuple[str, float]:
    """(edge_id, offset_m) of the edge closest to the point (for new_request by click).
    offset_m is measured along the edge (scaled to the edge `length`). Ties -> smallest edge_id."""
    kx = math.cos(math.radians(lat)) * 111320.0
    ky = 110574.0
    best: tuple[float, str, float] | None = None
    for u, v, k, d in G.edges(keys=True, data=True):
        if required_only and not d.get("required", True):
            continue
        g = d["geometry"]
        cum = 0.0
        geo_len = 0.0
        segs = []
        for i in range(len(g) - 1):
            ax, ay = (g[i][0] - lon) * kx, (g[i][1] - lat) * ky
            bx, by = (g[i + 1][0] - lon) * kx, (g[i + 1][1] - lat) * ky
            sl = math.hypot(bx - ax, by - ay)
            segs.append((ax, ay, bx, by, sl))
            geo_len += sl
        for ax, ay, bx, by, sl in segs:
            dx, dy = bx - ax, by - ay
            t = 0.0 if sl == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / (sl * sl)))
            dist = math.hypot(ax + t * dx, ay + t * dy)
            cand = (dist, d["edge_id"], cum + t * sl, geo_len)
            if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                best = (cand[0], cand[1], (cum + t * sl) / geo_len * d["length"] if geo_len > 0 else 0.0)
            cum += sl
    if best is None:
        raise ValueError("graph has no (matching) edges")
    return best[1], best[2]


def edges_geojson(G: nx.MultiDiGraph, tasks: list[Task] | None = None) -> dict:
    """FeatureCollection of edges (LineString) with properties id,name,required,task_id,status,
    slope_pct,length_m. Used by GET /api/graph/edges. `status` = task status (None if no task)."""
    by_task = {t.id: t for t in (tasks or [])}
    feats = []
    for u, v, k, d in sorted(G.edges(keys=True, data=True), key=lambda x: x[3]["edge_id"]):
        tid = d.get("task_id")
        t = by_task.get(tid) if tid else None
        status = None if t is None else getattr(t.status, "value", t.status)
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": d["geometry"]},
            "properties": {"id": d["edge_id"], "name": d.get("name"), "required": bool(d.get("required", True)),
                           "task_id": tid, "status": status,
                           "slope_pct": round(float(d.get("slope_pct", 0.0)), 2),
                           "length_m": round(float(d["length"]), 1)},
        })
    return {"type": "FeatureCollection", "features": feats}
