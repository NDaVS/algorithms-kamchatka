"""Step 3: combine raw OSM graph + elevations -> data/graph.json (with street pairing and tasks).

Run from backend/:  python -m scripts.build_graph
"""
from __future__ import annotations

import argparse

try:
    from scripts._common import BBOX, GRAPH_PATH, RAW_GRAPH_PATH, say
except ImportError:
    from _common import BBOX, GRAPH_PATH, RAW_GRAPH_PATH, say  # type: ignore


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    from core.graph import build_graph_from_osm, build_tasks, save_graph
    if not RAW_GRAPH_PATH.exists():
        say(f"Нет файла {RAW_GRAPH_PATH.name}: сначала выполните fetch_osm.")
        return 1
    import osmnx as ox
    say("Строю граф улиц ...")
    raw = ox.load_graphml(RAW_GRAPH_PATH)
    try:
        G = build_graph_from_osm(BBOX, raw=raw, with_elevation=True)
    except RuntimeError as e:
        say(f"ПРЕДУПРЕЖДЕНИЕ: {e}\n  Высоты недоступны, использую плоский рельеф (0 м).")
        G = build_graph_from_osm(BBOX, raw=raw, with_elevation=False)
    tasks = build_tasks(G)
    save_graph(G, GRAPH_PATH)
    say(f"Граф: {G.number_of_nodes()} узлов, {G.number_of_edges()} рёбер, задач (участков улиц): {len(tasks)}")
    say(f"База: узел {G.graph['depot_node']}, снегоотвал: узел {G.graph['dump_node']} -> {GRAPH_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
