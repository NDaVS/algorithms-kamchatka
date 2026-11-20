"""Build a local city map and machine-readable extract from OSM API XML."""

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROAD_TAGS = {
    "name", "name:ru", "highway", "ref", "oneway", "junction", "surface", "lanes",
    "width", "maxwidth", "maxheight", "maxweight", "maxaxleload", "incline",
    "maxspeed", "access", "vehicle", "motor_vehicle", "hgv", "foot", "service",
    "bridge", "tunnel", "layer", "winter_service", "smoothness", "area",
}
POINT_TYPES = {"hospital", "clinic", "fire_station", "school"}
POINT_TAGS = {"name", "name:ru", "highway", "amenity", "public_transport", "crossing"}
LAND_TYPES = {"wood", "grassland", "scrub", "water", "coastline"}


def tags(element):
    return {tag.attrib["k"]: tag.attrib["v"] for tag in element.findall("tag")}


def extract(files):
    coordinates, roads, land, points, restrictions = {}, {}, {}, {}, {}
    boxes = []
    for path in files:
        root = ET.parse(path).getroot()
        if root.tag != "osm":
            raise ValueError(f"Expected an OSM document: {path}")
        bounds = root.find("bounds").attrib
        boxes.append([float(bounds[key]) for key in ("minlon", "minlat", "maxlon", "maxlat")])
        for element in root:
            if element.tag not in {"node", "way", "relation"}:
                continue
            identifier = int(element.attrib["id"])
            source_tags = tags(element)
            if element.tag == "node":
                coordinates[identifier] = [
                    float(element.attrib["lon"]), float(element.attrib["lat"]),
                ]
                if source_tags.get("amenity") in POINT_TYPES or source_tags.get("highway") in {
                    "bus_stop", "crossing",
                }:
                    points[identifier] = {
                        key: value for key, value in source_tags.items() if key in POINT_TAGS
                    }
            elif element.tag == "way":
                refs = [int(nd.attrib["ref"]) for nd in element.findall("nd")]
                if "highway" in source_tags:
                    roads[identifier] = [refs, {key: value for key, value in source_tags.items() if key in ROAD_TAGS}]
                natural = source_tags.get("natural")
                leisure = source_tags.get("leisure")
                if natural in LAND_TYPES or leisure == "park" or "building" in source_tags:
                    kind = natural if natural in LAND_TYPES else "park" if leisure == "park" else "building"
                    land[identifier] = [kind, refs, source_tags.get("name")]
            elif source_tags.get("type") == "restriction":
                restrictions[identifier] = [
                    [member.attrib for member in element.findall("member")],
                    {key: value for key, value in source_tags.items()
                     if key.startswith("restriction") or key in {"type", "except"}},
                ]

    needed = set(points)
    for refs, _ in roads.values():
        needed.update(refs)
    for _, refs, _ in land.values():
        needed.update(refs)
    if missing := needed - coordinates.keys():
        raise ValueError(f"Missing geometry for {len(missing)} OSM nodes")

    return {
        "source": "OpenStreetMap", "license": "ODbL-1.0",
        "bounds": boxes,
        "nodes": {node: coordinates[node] for node in sorted(needed)},
        "ways": {way: roads[way] for way in sorted(roads)},
        "land": {way: land[way] for way in sorted(land)},
        "points": {point: points[point] for point in sorted(points)},
        "turn_restrictions": {key: restrictions[key] for key in sorted(restrictions)},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args()
    city = extract(sorted(args.files))
    payload = json.dumps(city, ensure_ascii=False, separators=(",", ":"))
    (ROOT / "data/petropavlovsk.json").write_text(payload + "\n", encoding="utf-8")
    (ROOT / "demo/petropavlovsk.js").write_text("window.petropavlovsk = " + payload + ";\n", encoding="utf-8")
    print(f"{len(city['nodes'])} nodes, {len(city['ways'])} ways, "
          f"{len(city['points'])} points, {len(city['turn_restrictions'])} restrictions")


if __name__ == "__main__":
    main()
