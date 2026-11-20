import json

import networkx as nx
import pytest

from core import elevation
from core.graph import (BBOX, build_graph_from_osm, build_tasks, edge_data, edge_position, edges_geojson,
                        haversine_m, load_graph, nearest_edge, parse_edge_id, shortest_path_edges)
from tests.synthetic import build_synthetic_city


@pytest.fixture(scope="module")
def city():
    return build_synthetic_city()


def test_shortest_path_contiguous(city):
    G = city.graph
    path = shortest_path_edges(G, city.depot_node, city.dump_node)
    assert path
    nodes = [parse_edge_id(e)[:2] for e in path]
    assert nodes[0][0] == city.depot_node and nodes[-1][1] == city.dump_node
    for (a, b), (c, d) in zip(nodes, nodes[1:]):
        assert b == c
    assert shortest_path_edges(G, 5, 5) == []


def test_shortest_path_asymmetric_and_modes(city):
    G = city.graph
    t = lambda p: sum(edge_data(G, e)["length"] for e in p)  # noqa: E731
    a = shortest_path_edges(G, 0, 155, "plow", "deadhead")
    b = shortest_path_edges(G, 0, 155, "truck", "deadhead", loaded=True)
    assert t(a) > 0 and t(b) > 0
    # one-way rows make some pairs asymmetric
    assert shortest_path_edges(G, 155, 0)  # strongly connected


def test_shortest_path_unreachable():
    G = nx.MultiDiGraph()
    G.add_node(1, x=0, y=0)
    G.add_node(2, x=0, y=0)
    with pytest.raises(nx.NetworkXNoPath):
        shortest_path_edges(G, 1, 2)


def test_edge_position(city):
    G = city.graph
    eid = next(iter(city.tasks[0].edge_ids))
    d = edge_data(G, eid)
    p0 = edge_position(G, eid, 0)
    p1 = edge_position(G, eid, 1e9)
    assert (p0.lon, p0.lat) == pytest.approx(tuple(d["geometry"][0]))
    assert (p1.lon, p1.lat) == pytest.approx(tuple(d["geometry"][-1]))
    assert p1.offset_m == pytest.approx(d["length"])
    pm = edge_position(G, eid, d["length"] / 2)
    assert pm.edge_id == eid and 0 <= pm.heading_deg < 360
    # midpoint is roughly equidistant (geometry can bend)
    assert haversine_m(p0.lon, p0.lat, pm.lon, pm.lat) < d["length"]


def test_nearest_edge(city):
    G = city.graph
    eid = city.tasks[10].edge_ids[0]
    d = edge_data(G, eid)
    mid = edge_position(G, eid, d["length"] * 0.3)
    got, off = nearest_edge(G, mid.lon, mid.lat)
    gd = edge_data(G, got)
    # same physical street (either direction)
    assert gd["street_id"] == d["street_id"]
    q = edge_position(G, got, off)
    assert haversine_m(q.lon, q.lat, mid.lon, mid.lat) < 2.0
    got2, _ = nearest_edge(G, mid.lon, mid.lat, required_only=True)
    assert edge_data(G, got2)["required"]


def test_edges_geojson(city):
    fc = edges_geojson(city.graph, city.tasks)
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == city.graph.number_of_edges()
    f = fc["features"][0]
    assert set(f["properties"]) == {"id", "name", "required", "task_id", "status", "slope_pct", "length_m"}
    assert f["geometry"]["type"] == "LineString"
    assert {x["properties"]["status"] for x in fc["features"] if x["properties"]["task_id"]} == {"pending"}
    assert any(x["properties"]["status"] is None for x in edges_geojson(city.graph)["features"])
    json.dumps(fc, ensure_ascii=False)


def test_build_graph_from_raw_osm_like():
    """Raw osmnx-like graph (no osmnx needed): two-way street, one-way street, service road."""
    raw = nx.MultiDiGraph()
    pts = {100: (158.61, 53.01), 200: (158.62, 53.01), 300: (158.62, 53.02), 400: (158.67, 53.06)}
    for n, (x, y) in pts.items():
        raw.add_node(n, x=x, y=y)

    def add(u, v, hw, name, length):
        raw.add_edge(u, v, length=length, highway=hw, name=name)
    add(100, 200, "residential", "улица А", 650.0)
    add(200, 100, "residential", "улица А", 650.0)
    add(200, 300, "secondary", ["улица Б", "x"], 1100.0)
    add(300, 200, "secondary", ["улица Б", "x"], 1100.0)
    add(300, 400, "service", None, 6000.0)
    add(400, 300, "service", None, 6000.0)
    G = build_graph_from_osm(BBOX, raw=raw, with_elevation=False,
                             depot_lonlat=(158.61, 53.01), dump_lonlat=(158.67, 53.06))
    assert G.number_of_nodes() == 4 and G.number_of_edges() == 6
    assert G.nodes[G.graph["depot_node"]]["kind"] == "depot"
    assert G.nodes[G.graph["dump_node"]]["kind"] == "dump"
    for u, v, k, d in G.edges(keys=True, data=True):
        assert d["edge_id"] == f"{u}-{v}-{k}"
        assert d["reverse_edge_id"] and not d["oneway"]
        assert d["geometry"][0] == [G.nodes[u]["x"], G.nodes[u]["y"]]
    req = {d["highway"]: d["required"] for _, _, d in G.edges(data=True)}
    assert req == {"residential": True, "secondary": True, "service": False}
    tasks = build_tasks(G)
    assert len(tasks) == 2 and all(len(t.edge_ids) == 2 for t in tasks)


def test_elevation_cache_and_slope(tmp_path, monkeypatch):
    cache = tmp_path / "c.json"
    monkeypatch.setattr(elevation, "CACHE_PATH", cache)
    monkeypatch.setattr(elevation, "load_cache", lambda path=cache: elevation.load_cache.__wrapped__(path)
                        if hasattr(elevation.load_cache, "__wrapped__") else
                        {k: float(v) for k, v in json.loads(cache.read_text()).items()} if cache.exists() else {})
    cache.write_text(json.dumps({"158.61000,53.01000": 10.0, "158.62000,53.01000": 30.0}))
    monkeypatch.setattr(elevation, "save_cache", lambda c, path=cache: cache.write_text(json.dumps(c)))
    assert elevation.get_elevations([(158.61, 53.01), (158.62, 53.01)]) == [10.0, 30.0]

    def boom(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr(elevation, "fetch_open_meteo", boom)
    with pytest.raises(RuntimeError):
        elevation.get_elevations([(158.7, 53.0)])

    G = nx.MultiDiGraph()
    G.add_node(0, x=158.61, y=53.01)
    G.add_node(1, x=158.62, y=53.01)
    G.add_edge(0, 1, 0, length=100.0)
    G.add_edge(1, 0, 0, length=100.0)
    elevation.attach_elevation(G)
    assert G[0][1][0]["slope_pct"] == pytest.approx(20.0)
    assert G[1][0][0]["slope_pct"] == pytest.approx(-20.0)


def test_generated_data_loads(tmp_path):
    from core.graph import GRAPH_PATH
    if GRAPH_PATH.exists():
        G = load_graph(GRAPH_PATH)
        assert G.number_of_edges() > 0
