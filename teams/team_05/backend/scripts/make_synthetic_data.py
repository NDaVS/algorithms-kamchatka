"""Offline fallback: write data/graph.json + data/basemap.geojson from tests.synthetic (no internet needed).

Run from backend/:  python -m scripts.make_synthetic_data
"""
from __future__ import annotations

import argparse
import random

try:
    from scripts._common import BASEMAP_PATH, BBOX, GRAPH_PATH, say, write_basemap
except ImportError:
    from _common import BASEMAP_PATH, BBOX, GRAPH_PATH, say, write_basemap  # type: ignore


def _poly(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def synthetic_basemap(city, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    lon0, lat0, lon1, lat1 = BBOX
    feats: list[dict] = []
    seen = set()
    for e in city.edges:  # roads (one line per physical street)
        if e.street_id in seen:
            continue
        seen.add(e.street_id)
        feats.append({"type": "Feature", "properties": {"layer": "roads", "highway": e.highway, "name": e.name},
                      "geometry": {"type": "LineString", "coordinates": e.geometry}})
    # water: a "bay" strip along the east side + a pond
    bay = [[lon1 - 0.0025, lat0 + 0.012], [lon1, lat0 + 0.012], [lon1, lat1 - 0.02], [lon1 - 0.0018, lat1 - 0.02],
           [lon1 - 0.003, lat0 + 0.03], [lon1 - 0.0025, lat0 + 0.012]]
    feats.append({"type": "Feature", "properties": {"layer": "water"},
                  "geometry": {"type": "Polygon", "coordinates": [bay]}})
    feats.append({"type": "Feature", "properties": {"layer": "water"},
                  "geometry": _poly(lon0 + 0.030, lat0 + 0.0405, lon0 + 0.034, lat0 + 0.0425)})
    # parks and buildings in block interiors (cells between node rows/cols)
    nb = {n.id: n for n in city.nodes}
    cols = 13
    rows = len(city.nodes) // cols
    for r in range(1, rows - 1):
        for c in range(cols - 1):
            a, b, d = nb[r * cols + c], nb[r * cols + c + 1], nb[(r + 1) * cols + c]
            x0, x1 = a.lon + 0.0004, b.lon - 0.0004
            y0, y1 = a.lat + 0.0003, d.lat - 0.0003
            if x1 <= x0 or y1 <= y0:
                continue
            if rng.random() < 0.12:
                feats.append({"type": "Feature", "properties": {"layer": "parks"}, "geometry": _poly(x0, y0, x1, y1)})
                continue
            for _ in range(rng.randint(1, 3)):
                w, h = rng.uniform(0.0004, 0.0010), rng.uniform(0.0002, 0.0005)
                bx = rng.uniform(x0, max(x0, x1 - w))
                by = rng.uniform(y0, max(y0, y1 - h))
                feats.append({"type": "Feature", "properties": {"layer": "buildings"},
                              "geometry": _poly(round(bx, 6), round(by, 6), round(bx + w, 6), round(by + h, 6))})
    return feats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args(argv)
    from core.graph import save_graph
    from tests.synthetic import build_synthetic_city
    say("Создаю синтетический город (офлайн-данные) ...")
    city = build_synthetic_city(seed=a.seed)
    save_graph(city.graph, GRAPH_PATH)
    write_basemap(synthetic_basemap(city), BASEMAP_PATH)
    say(f"Готово: {GRAPH_PATH.name} ({city.graph.number_of_nodes()} узлов, {city.graph.number_of_edges()} рёбер, "
        f"{len(city.tasks)} задач) и {BASEMAP_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
