"""Step 1: download OSM data for the MVP bbox (needs internet + osmnx).

Writes data/osm_drive.graphml (raw drive network) and data/basemap.geojson (roads/water/parks/buildings).
Run from backend/:  python -m scripts.fetch_osm
"""
from __future__ import annotations

import argparse

try:
    from scripts._common import BBOX, LAYERS, RAW_GRAPH_PATH, log_exc, say, write_basemap
except ImportError:  # run as a plain file: python scripts/fetch_osm.py
    from _common import BBOX, LAYERS, RAW_GRAPH_PATH, log_exc, say, write_basemap  # type: ignore

SKIP_ROADS = {"footway", "steps", "path", "cycleway", "pedestrian", "bridleway", "corridor", "elevator", "proposed"}
TAGS = {
    "water": {"natural": ["water", "bay"], "waterway": ["riverbank"], "water": True},
    "parks": {"landuse": ["forest", "grass", "meadow", "recreation_ground", "village_green"],
              "natural": ["wood", "scrub", "grassland"], "leisure": ["park", "garden"]},
    "buildings": {"building": True},
    "roads": {"highway": True},
}
SIMPLIFY = {"roads": 0.000008, "water": 0.00002, "parks": 0.00002, "buildings": 0.000008}


def _features(tags: dict):
    import osmnx as ox
    west, south, east, north = BBOX
    try:
        return ox.features_from_bbox(bbox=(west, south, east, north), tags=tags)  # osmnx 2.x
    except TypeError:
        return ox.features_from_bbox(north, south, east, west, tags=tags)  # osmnx 1.x


def _round(obj, nd=6):
    if isinstance(obj, (list, tuple)):
        return [_round(o, nd) for o in obj]
    return round(obj, nd)


def layer_features(layer: str) -> list[dict]:
    from shapely.geometry import box, mapping
    clip = box(*BBOX)
    try:
        gdf = _features(TAGS[layer])
    except Exception as e:  # noqa: BLE001  (InsufficientResponseError = nothing found)
        say(f"  слой «{layer}»: данных нет ({type(e).__name__})")
        return []
    out = []
    for _, row in gdf.iterrows():
        g = row.geometry
        if g is None or g.is_empty:
            continue
        if layer == "roads":
            hw = row.get("highway")
            hw = hw[0] if isinstance(hw, list) else hw
            if not isinstance(hw, str) or hw in SKIP_ROADS or g.geom_type not in ("LineString", "MultiLineString"):
                continue
        elif g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        try:
            g = g.intersection(clip).simplify(SIMPLIFY[layer], preserve_topology=True)
        except Exception:  # noqa: BLE001
            continue
        if g.is_empty:
            continue
        want = ("LineString", "MultiLineString") if layer == "roads" else ("Polygon", "MultiPolygon")
        parts = [g] if g.geom_type in want else [p for p in getattr(g, "geoms", []) if p.geom_type in want]
        for p in parts:
            m = mapping(p)
            props = {"layer": layer}
            if layer == "roads":
                nm = row.get("name")
                props.update(highway=hw, name=nm if isinstance(nm, str) else None)
            out.append({"type": "Feature", "properties": props,
                        "geometry": {"type": m["type"], "coordinates": _round(m["coordinates"])}})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-graph", action="store_true", help="только базовая карта")
    ap.add_argument("--no-basemap", action="store_true", help="только граф дорог")
    a = ap.parse_args(argv)
    try:
        import osmnx as ox
    except ImportError:
        say("ОШИБКА: не установлен osmnx (pip install -r requirements.txt).")
        return 2
    say(f"Скачиваю данные OpenStreetMap для области {BBOX} ...")
    try:
        if not a.no_graph:
            from core.graph import _download_osm
            say("  Дорожная сеть (drive) ...")
            G = _download_osm(BBOX)
            RAW_GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
            ox.save_graphml(G, RAW_GRAPH_PATH)
            say(f"  Готово: {G.number_of_nodes()} узлов, {G.number_of_edges()} рёбер -> {RAW_GRAPH_PATH.name}")
        if not a.no_basemap:
            feats: list[dict] = []
            for layer in LAYERS:
                say(f"  Слой «{layer}» ...")
                f = layer_features(layer)
                say(f"    объектов: {len(f)}")
                feats += f
            write_basemap(feats)
            say("  Базовая карта сохранена: data/basemap.geojson")
    except Exception as e:  # noqa: BLE001
        say(f"ОШИБКА загрузки OSM: {type(e).__name__}: {e}")
        log_exc("fetch_osm")
        say("Проверьте интернет или используйте офлайн-данные: python -m scripts.make_synthetic_data")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
