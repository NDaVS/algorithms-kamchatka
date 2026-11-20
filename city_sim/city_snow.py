"""Deterministic tiled replay over the bounded Petropavlovsk urban scene."""
from collections import Counter
from datetime import datetime
from functools import cached_property
import gzip
from hashlib import file_digest
import json
from pathlib import Path
import threading
from typing import Literal
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from shapely import intersects_xy, union_all
from shapely.affinity import scale, translate
from shapely.geometry import LineString, box, shape

from weather_coverage.geography import read_osm, relation_polygon
from winter_audit.relief import distance
from .building_evidence import load_json
from .geodata import ROOT, polygons
from .operations import POLICY, ZONE, digest, weather_status
from .snow_experiment import ENGINE_LOCK, overlay, rows_json
from .terrain import BOUNDS, DATA

STORMS_PATH = DATA / "storm-weather.json.gz"
BOUNDARY = ROOT / "weather_coverage/osm/city-boundary.osm.gz"
TILE_DEG = (.022, .014)
TILE_CELLS = 64
METHOD = "city-snow-4"
OPENWEATHER_URL = "https://openweathermap.org/api/forecast5"
LOCAL_WEATHER_M = 3000
MAX_WEATHER_M = 15000


class CitySnowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    source: Literal["openweather", "reanalysis", "manual"] = "openweather"
    valid_at: AwareDatetime | None = None
    event_id: str | None = None
    snow_to_liquid_ratio: float = Field(default=10, ge=2, le=30)
    wind_from_deg: float = Field(default=270, ge=0, lt=360)
    wind_speed_ms: float = Field(default=8, ge=0, le=70)
    snowfall_cm_h: float = Field(default=2, ge=0, le=30)
    hours: int = Field(default=3, ge=1, le=6)

    @model_validator(mode="after")
    def matching_source(self):
        if self.source == "openweather" and self.valid_at is None:
            raise ValueError("Выберите срок OpenWeather.")
        if self.source == "reanalysis" and not self.event_id:
            raise ValueError("Выберите исторический эпизод.")
        if self.valid_at:
            self.valid_at = self.valid_at.astimezone(ZONE)
        return self


def file_hash(path):
    with path.open("rb") as handle:
        return file_digest(handle, "sha256").hexdigest()


def administrative_boundary():
    osm = read_osm([BOUNDARY])
    boundary = osm["r4139922"]
    nodes = {key: (float(elem.get("lon")), float(elem.get("lat")))
             for key, elem in osm.items() if key.startswith("n")}
    lines = {}
    for member in boundary.findall("member"):
        if member.get("type") != "way":
            continue
        key = "w" + member.get("ref")
        lines[key] = LineString([nodes["n"+ref.get("ref")] for ref in osm[key].findall("nd")])
    result = relation_polygon(boundary, lines, strict=True)
    if result is None or result.is_empty:
        raise ValueError("Граница Петропавловска-Камчатского недоступна.")
    return result


def remap(raw, count=TILE_CELLS):
    """Resample by destination cell centers, preserving masked source cells."""
    data = np.asarray(raw, dtype=float)
    indices = [np.minimum((np.arange(count)+.5)*cells/count, cells-1).astype(int) for cells in data.shape]
    return data[np.ix_(*indices)]


class CitySnow:
    def __init__(self, study, output):
        self.study = study
        self.city = study.city
        self.archive = study.archive
        self.output = Path(output)
        self.jobs = {}
        self.lock = threading.Lock()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="city-snow")

    @cached_property
    def storms(self):
        return load_json(STORMS_PATH, {})

    @cached_property
    def terms(self):
        by_term = {}
        for row in self.archive.samples.values():
            stamp = row.weather.valid_at
            if row.weather.kind != "forecast" or row.weather.retrieved_at > stamp:
                continue
            samples = by_term.setdefault(stamp, {})
            old = samples.get(row.point_id)
            if not old or row.weather.retrieved_at > old.weather.retrieved_at:
                samples[row.point_id] = row
        return {stamp: list(samples.values()) for stamp, samples in by_term.items()}

    def catalog(self):
        return {
            "openweather": [{"valid_at": stamp.astimezone(ZONE).isoformat(), "points": len(samples)}
                            for stamp, samples in sorted(self.terms.items())],
            "storms": [{"event_id": key, "title": value["title"], "start_at": value["start_at"],
                        "end_at": value["end_at"], "source_cells": len(value["locations"])}
                       for key, value in self.storms.items()],
            "bounds": BOUNDS, "tile_cells": TILE_CELLS,
        }

    def weather(self, spec):
        def state(direction, speed, snow):
            return {"wind_from_deg": direction, "wind_speed_ms": speed, "snowfall_cm": snow}

        if spec.source == "manual":
            hours = [state(spec.wind_from_deg, spec.wind_speed_ms, spec.snowfall_cm_h)] * spec.hours
            return lambda p: ("manual", hours, None), {"kind": "manual", "input": spec.model_dump(mode="json")}
        if spec.source == "reanalysis":
            event = self.storms.get(spec.event_id)
            if event is None:
                raise ValueError("Исторический эпизод не найден.")
            def at(point):
                sample = min(event["locations"], key=lambda row: distance(point, row["coordinates"]))
                meters = distance(point, sample["coordinates"])
                if meters > 18000:
                    return None
                return (str(sample["coordinates"]), sample["hours"], round(meters))
            return at, event

        forecasts = self.terms.get(spec.valid_at, [])
        if not forecasts:
            raise ValueError("Для этого срока OpenWeather в локальном атласе нет записей.")
        g500 = [r for r in forecasts if r.point_id.startswith("g500-")]
        candidates = g500 or forecasts
        extra = [r for r in forecasts if r not in candidates]
        def at(point):
            samples = [(distance(point, row.weather.coordinate_lon_lat), row) for row in candidates]
            meters, row = min(samples, key=lambda pair: (pair[0], pair[1].sample_id))
            if meters > LOCAL_WEATHER_M and extra:
                meters, row = min([(meters, row), *((distance(point, r.weather.coordinate_lon_lat), r) for r in extra)],
                                  key=lambda pair: (pair[0], pair[1].sample_id))
            if meters > MAX_WEATHER_M:
                return None
            # A valid Forecast response omits the snow object when there is no snow.
            amount = (row.snow_mm_3h or 0) * spec.snow_to_liquid_ratio / 10
            data = state(row.wind.direction_from_deg, row.wind.speed_mps, amount)
            data.update(sample_id=row.sample_id, point_id=row.point_id,
                        valid_at=row.weather.valid_at.isoformat(),
                        source_time=row.weather.retrieved_at.isoformat(),
                        coordinates=row.weather.coordinate_lon_lat, snow_mm_3h=row.snow_mm_3h,
                        provider_coordinates=row.weather.provider_coordinate_lon_lat,
                        gust_mps=row.gust_mps,
                        snow_field_omitted=row.snow_mm_3h is None)
            return (row.sample_id, [data], round(meters))
        provider_coordinates = {r.weather.provider_coordinate_lon_lat for r in forecasts
                                if r.weather.provider_coordinate_lon_lat is not None}
        return at, {"kind": "openweather_forecast", "url": OPENWEATHER_URL,
                    "valid_at": spec.valid_at.isoformat(), "snapshot_points": len(forecasts),
                    "provider_coordinates_reported": len(provider_coordinates),
                    "selection_points": len(candidates), "snow_to_liquid_ratio": spec.snow_to_liquid_ratio,
                    "local_distance_m": LOCAL_WEATHER_M, "max_distance_m": MAX_WEATHER_M,
                    "signal_policy": POLICY,
                    "atlas_sha256": sorted({r.weather.data_revision for r in forecasts})}

    def identity(self, spec, source):
        return digest({"input": spec.model_dump(mode="json"), "weather": source,
                       "dataset": self.city.data_revision, "model": ENGINE_LOCK,
                       "boundary": file_hash(BOUNDARY), "method": METHOD})

    def _path(self, run_id, extension):
        if len(run_id) != 64 or any(c not in "0123456789abcdef" for c in run_id):
            raise ValueError("Некорректный идентификатор расчёта.")
        return self.output / f"{run_id}.{extension}"

    def replay_path(self, run_id):
        return self._path(run_id, "json.gz")

    def submit(self, spec):
        self.study.engine  # Fail early if the checked model is unavailable.
        select, source = self.weather(spec)
        run_id = self.identity(spec, source)
        with self.lock:
            if self._path(run_id, "json").is_file():
                return {"run_id": run_id, "status": "completed"}
            prior = self.jobs.get(run_id)
            if prior and prior["status"] in {"pending", "running"}:
                return {"run_id": run_id, **prior}
            if sum(j["status"] in {"pending", "running"} for j in self.jobs.values()) >= 3:
                raise RuntimeError("Очередь городских расчётов заполнена.")
            self.jobs[run_id] = {"status": "pending", "completed_tiles": 0, "total_tiles": None}
            while len(self.jobs) > 20:
                inactive = next((key for key, value in self.jobs.items()
                                 if value["status"] not in {"pending", "running"}), None)
                if inactive is None:
                    break
                del self.jobs[inactive]
            self.worker.submit(self._calculate, run_id, spec, select, source)
        return {"run_id": run_id, "status": "pending"}

    def status(self, run_id):
        path = self._path(run_id, "json")
        if path.is_file():
            return json.loads(path.read_text())
        with self.lock:
            return {"run_id": run_id, **self.jobs[run_id]} if run_id in self.jobs else None

    def _calculate(self, run_id, spec, select, source):
        import logging

        def progress(done, total):
            with self.lock:
                self.jobs[run_id] = {"status": "running", "completed_tiles": done, "total_tiles": total}
        try:
            result, full = self.run(spec, select, source, progress)
            self.output.mkdir(parents=True, exist_ok=True)
            replay_path = self._path(run_id, "json.gz")
            tmp = replay_path.with_suffix(".tmp")
            tmp.write_bytes(gzip.compress(json.dumps(full, allow_nan=False, separators=(",", ":")).encode(), mtime=0))
            tmp.replace(replay_path)
            summary = self._path(run_id, "json")
            tmp = summary.with_suffix(".tmp")
            tmp.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))
            tmp.replace(summary)
            with self.lock:
                self.jobs[run_id] = {"status": "completed"}
        except Exception:
            logging.exception("Citywide snow scenario failed: %s", run_id)
            with self.lock:
                self.jobs[run_id] = {"status": "failed", "error": "Городской расчёт завершился ошибкой. См. журнал API."}

    def run(self, spec, select=None, source=None, progress=None):
        if select is None:
            select, source = self.weather(spec)
        city = self.city
        progress = progress or (lambda a, b: None)
        run_id = self.identity(spec, source)
        model, engine = self.study.engine
        west, south, east, north = BOUNDS
        urban = administrative_boundary().intersection(box(*BOUNDS))
        water = union_all([shape(f["geometry"]) for f in city.osm_layers[1]["features"]
                           if f["properties"]["kind"] == "water"])
        land = urban.difference(water)
        nx = int(np.ceil((east-west)/TILE_DEG[0]))
        ny = int(np.ceil((north-south)/TILE_DEG[1]))
        xcorners = np.linspace(west, east, nx+1)
        ycorners = np.linspace(north, south, ny+1)
        bands = ["snow_cm", "combined", "opposite", "uniform", "terrain", "weather_snow", "weather_wind",
                 "weather_distance"]
        fields = {key: np.full((ny*TILE_CELLS, nx*TILE_CELLS), np.nan) for key in bands}
        stripes = []
        for j in range(ny):
            for i in range(nx):
                bounds = (float(xcorners[i]), float(ycorners[j+1]), float(xcorners[i+1]), float(ycorners[j]))
                if land.intersection(box(*bounds)).area > 0:
                    stripes.append((i, j, bounds))
        progress(0, len(stripes))
        tile_inputs, signals, sources_found = [], [], set()
        stats = Counter()
        distance_max = None
        for done, (i, j, bounds) in enumerate(stripes, 1):
            w, s, e, n = bounds
            selected = select(((w+e)/2, (s+n)/2))
            if not selected:
                stats["missing_weather_tiles"] += 1
                progress(done, len(stripes))
                continue
            source_key, hours, point_distance = selected
            sources_found.add(source_key)
            tile = {"i": i, "j": j, "distance_to_source_m": point_distance, "hours": hours}
            tile_inputs.append(tile)
            if point_distance is not None:
                distance_max = max(point_distance, distance_max or 0)
            if spec.source == "openweather":
                row = hours[0]
                extrapolated = point_distance > LOCAL_WEATHER_M
                stats["extrapolated_weather_tiles"] += extrapolated
                signal, reasons = weather_status(row["snow_mm_3h"] or 0, row["wind_speed_ms"], row["gust_mps"])
                tile.update(weather_signal=signal, signal_reasons=reasons, extrapolated=extrapolated)
                if signal == "inspect":
                    stats["weather_alert_tiles"] += 1
                    stats["weather_alert_tiles_local"] += not extrapolated
                    signals.append({"coordinates": [(w+e)/2, (s+n)/2], "distance_to_source_m": point_distance,
                                    "reasons": reasons, "extrapolated": extrapolated})
            sx, sy, _, _ = engine.projection(bounds)
            neighbors = city.buildings((w-360/sx, s-360/sy, e+360/sx, n+360/sy))["features"]
            buildings = []
            for item in neighbors:
                props = item["properties"]
                for polygon in polygons(shape(item["geometry"])):
                    local = scale(translate(polygon, xoff=-w, yoff=-s), xfact=sx, yfact=sy, origin=(0, 0))
                    if local.area >= 4:
                        buildings.append(engine.Building(local, props["height_m"] or 9., props["height_source"]))
            opposite = [{**row, "wind_from_deg": (row["wind_from_deg"]+180) % 360} for row in hours]
            direct = model.estimate(bounds, buildings, hours, TILE_CELLS, True)
            reverse = model.estimate(bounds, buildings, opposite, TILE_CELLS, True)
            total = sum(h["snowfall_cm"] for h in hours)
            speeds = [h["wind_speed_ms"] for h in hours]
            coords = (np.arange(TILE_CELLS)+.5)/TILE_CELLS
            x, y = np.meshgrid(w+(e-w)*coords, n-(n-s)*coords)
            ground = intersects_xy(land, x, y)
            actual = remap(direct["snow_depth_cm"])
            alt = remap(reverse["snow_depth_cm"])
            valid = ground & np.isfinite(actual)
            center = city.terrain.sample(x, y)
            surrounding = np.asarray([city.terrain.sample(x+dx/sx, y+dy/sy)
                                       for dx, dy in ((90, 0), (-90, 0), (0, 90), (0, -90))])
            relief = np.clip((surrounding.mean(axis=0)-center)/12, 0, 1)
            def risk(values):
                return (.75*np.clip((values/total-1)/1.5, 0, 1)+.25*np.nan_to_num(relief)
                        if total > 0 and max(speeds) > 2 else np.zeros_like(values))
            target = np.s_[j*TILE_CELLS:(j+1)*TILE_CELLS, i*TILE_CELLS:(i+1)*TILE_CELLS]
            values = (actual, risk(actual), risk(alt), 0., relief, total, max(speeds),
                      point_distance/1000 if point_distance is not None else np.nan)
            for key, values in zip(bands, values):
                fields[key][target] = np.where(valid, values, np.nan)
            stats["computed_tiles"] += 1
            stats["dry_tiles"] += total == 0
            stats["hybrid_tiles"] += direct["model"] == "hybrid"
            stats["hybrid_partial_tiles"] += direct["model"] == "hybrid_partial"
            stats["ground_cells_computed"] += int(valid.sum())
            progress(done, len(stripes))

        rows, cols = fields["combined"].shape
        roads = city.roads(BOUNDS, extended=True)
        seen, rankings = set(), {key: [] for key in ("combined", "opposite", "terrain", "uniform")}
        coverage_m = total_road_m = alert_road_m = weather_local_road_m = 0
        weather_by_tile = {(tile["i"], tile["j"]): tile for tile in tile_inputs}
        for item in sorted(roads["features"], key=lambda f: f["id"]):
            p = item["properties"]
            physical = p["physical_road_id"]
            if physical in seen:
                continue
            seen.add(physical)
            a, b = item["geometry"]["coordinates"]
            lon, lat = [(a+b)/2 for a, b in zip(a, b)]
            if not (west <= lon < east and south < lat <= north and intersects_xy(urban, lon, lat)):
                continue
            total_road_m += p["length_m"]
            if p["network_extension"]:
                stats["extended_road_m_in_city"] += p["length_m"]
            i = int((lon-west)/(east-west)*cols)
            j = int((north-lat)/(north-south)*rows)
            if not np.isfinite(fields["combined"][j, i]):
                continue
            coverage_m += p["length_m"]
            if p["network_extension"]:
                stats["extended_road_m_covered"] += p["length_m"]
            tile = weather_by_tile[(i//TILE_CELLS, j//TILE_CELLS)]
            if tile.get("weather_signal") == "inspect":
                alert_road_m += p["length_m"]
            if tile.get("extrapolated") is False:
                weather_local_road_m += p["length_m"]
            stats["road_segments_covered"] += 1
            for name in rankings:
                value = fields[name][j, i]
                if np.isfinite(value) and (value > 0 or name in {"terrain", "uniform"}):
                    rankings[name].append({"edge_id": item["id"], "name": p["road_name"], "lon": lon, "lat": lat,
                                           "value": round(float(value), 4),
                                           "network_extension": p["network_extension"],
                                           "fresh_snow_cm": round(float(fields["snow_cm"][j, i]), 3),
                                           "excess_cm": round(float(fields["snow_cm"][j, i]-fields["weather_snow"][j, i]), 3)
                                           if name == "combined" else None})
        for key in rankings:
            rankings[key] = sorted(rankings[key], key=lambda r: (-r["value"], r["edge_id"]))[:20]
        fit = land.bounds
        metrics = {"eligible_tiles": len(stripes), "distinct_weather_inputs": len(sources_found),
                   "road_m_in_city": total_road_m, "road_m_covered": coverage_m,
                   "road_m_weather_alert": alert_road_m if spec.source == "openweather" else None,
                   "road_m_weather_local": weather_local_road_m if spec.source == "openweather" else None,
                   "max_distance_to_weather_m": distance_max, **stats}
        raw = {"run_id": run_id, "status": "completed", "bounds": BOUNDS, "fit_bounds": fit,
               "input": spec.model_dump(mode="json"), "source": source, "district": "Весь город",
               "tile_cells": TILE_CELLS, "rows": rows, "cols": cols, "metrics": metrics,
               "field_validated": False, "building_sides": {}, "ranking": rankings, "weather_alerts": signals,
               "provenance": {"engine_commit": ENGINE_LOCK["commit"], "engine_sha256": ENGINE_LOCK["sha256"],
                              "city_revision": city.data_revision, "boundary_sha256": file_hash(BOUNDARY),
                              "graph_version": roads["metadata"]["graph_version"], "algorithm": METHOD}}
        scales = {"snow_cm": 30, "weather_snow": 30, "weather_wind": 20, "weather_distance": 15}
        result = {**raw, "scales": scales, "overlays": {
            key: overlay(np.clip(field/scales.get(key, 1), 0, 1)) for key, field in fields.items()}}
        replay = {**raw, "tiles": tile_inputs, "grids": {k: rows_json(v) for k, v in fields.items()}}
        return result, replay
