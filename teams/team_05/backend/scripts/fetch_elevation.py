"""Step 2: fetch elevations of all graph nodes (Open-Meteo, 100 points per request) into
data/elevation_cache.json (key "lon,lat" 5dp). Offline => warning, graph will get flat 0 elevation.

Run from backend/:  python -m scripts.fetch_elevation
"""
from __future__ import annotations

import argparse

try:
    from scripts._common import RAW_GRAPH_PATH, say
except ImportError:
    from _common import RAW_GRAPH_PATH, say  # type: ignore


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    import networkx as nx
    from core.elevation import cache_key, get_elevations, load_cache
    if not RAW_GRAPH_PATH.exists():
        say(f"Нет файла {RAW_GRAPH_PATH.name}: сначала выполните fetch_osm. Пропускаю высоты.")
        return 1
    G = nx.read_graphml(RAW_GRAPH_PATH)
    pts = [(float(d["x"]), float(d["y"])) for _, d in G.nodes(data=True)]
    cache = load_cache()
    missing = sum(1 for p in pts if cache_key(*p) not in cache)
    say(f"Высоты: узлов {len(pts)}, в кэше уже {len(pts) - missing}, нужно загрузить {missing}.")
    if missing:
        say(f"  Запросов к Open-Meteo: ~{(missing + 99) // 100} (с паузами, это займёт немного времени) ...")
    try:
        get_elevations(pts)
    except RuntimeError as e:
        say(f"ПРЕДУПРЕЖДЕНИЕ: {e}")
        say("  Работаю офлайн: высоты будут равны 0 (плоский рельеф), уклоны = 0%.")
        return 1
    say("Высоты сохранены в data/elevation_cache.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
