"""Elevation + slope. OWNER: data agent. See CONTRACTS.md section 5.

Sources, in order: data/elevation_cache.json -> data/dem.tif (rasterio, optional) -> Open-Meteo online API
(batched by 100, retried, cached). No top-level third-party imports besides networkx.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import networkx as nx

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_PATH = DATA_DIR / "elevation_cache.json"
DEM_PATH = DATA_DIR / "dem.tif"
API_URL = "https://api.open-meteo.com/v1/elevation"
BATCH = 100


def slope_pct(z_from: float, z_to: float, length_m: float) -> float:
    """Slope in percent for travelling from->to (positive = uphill). Clamped to +-25."""
    if length_m <= 0:
        return 0.0
    return max(-25.0, min(25.0, (z_to - z_from) / length_m * 100.0))


def cache_key(lon: float, lat: float) -> str:
    return f"{lon:.5f},{lat:.5f}"


def load_cache(path: Path = CACHE_PATH) -> dict[str, float]:
    if Path(path).exists():
        try:
            return {k: float(v) for k, v in json.loads(Path(path).read_text(encoding="utf-8")).items()}
        except (ValueError, OSError):
            return {}
    return {}


def save_cache(cache: dict[str, float], path: Path = CACHE_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def _from_dem(points: list[tuple[float, float]]) -> list[float | None]:
    if not DEM_PATH.exists():
        return [None] * len(points)
    try:
        import rasterio  # lazy
    except ImportError:
        return [None] * len(points)
    out: list[float | None] = []
    with rasterio.open(DEM_PATH) as ds:
        nodata = ds.nodata
        for (lon, lat), val in zip(points, ds.sample(points)):
            z = float(val[0])
            out.append(None if (nodata is not None and z == nodata) or z != z else z)
    return out


def fetch_open_meteo(points: list[tuple[float, float]], delay_s: float = 0.4, retries: int = 4,
                     log=print) -> list[float]:
    """Query Open-Meteo for [(lon,lat),...] in batches of 100. Raises RuntimeError on failure."""
    result: list[float] = []
    for i in range(0, len(points), BATCH):
        chunk = points[i:i + BATCH]
        qs = urllib.parse.urlencode({"latitude": ",".join(f"{lat:.5f}" for _, lat in chunk),
                                     "longitude": ",".join(f"{lon:.5f}" for lon, _ in chunk)}, safe=",")
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(f"{API_URL}?{qs}", headers={"User-Agent": "snegoplan-data/1.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode("utf-8"))
                els = data["elevation"]
                if len(els) != len(chunk):
                    raise ValueError("unexpected response length")
                result.extend(float(z) for z in els)
                last_err = None
                break
            except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                last_err = e
                wait = 2.0 * (attempt + 1)
                log(f"  ! ошибка запроса высот ({e}); повтор через {wait:.0f} с")
                time.sleep(wait)
        if last_err is not None:
            raise RuntimeError(f"Не удалось получить высоты от Open-Meteo: {last_err}")
        time.sleep(delay_s)
    return result


def get_elevations(lonlats: list[tuple[float, float]]) -> list[float]:
    """Elevations (m) for [(lon,lat),...]. Order: 1) data/elevation_cache.json (key "lon,lat"
    rounded 5dp), 2) local DEM GeoTIFF data/dem.tif if present, 3) online API (Open-Meteo, batched,
    then cached). Offline & nothing cached => raise RuntimeError."""
    cache = load_cache()
    keys = [cache_key(lon, lat) for lon, lat in lonlats]
    missing = sorted({i for i, k in enumerate(keys) if k not in cache}, key=lambda i: keys[i])
    # unique missing points
    seen: dict[str, int] = {}
    for i in missing:
        seen.setdefault(keys[i], i)
    todo = [(k, lonlats[i]) for k, i in seen.items()]
    if todo:
        pts = [p for _, p in todo]
        dem = _from_dem(pts)
        still = []
        for (k, p), z in zip(todo, dem):
            if z is None:
                still.append((k, p))
            else:
                cache[k] = z
        if still:
            zs = fetch_open_meteo([p for _, p in still])  # may raise RuntimeError
            for (k, _), z in zip(still, zs):
                cache[k] = z
        save_cache(cache)
    return [cache[k] for k in keys]


def attach_elevation(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Set node attr `elevation` for all nodes and edge attr `slope_pct` (use slope_pct())."""
    ids = list(G.nodes)
    zs = get_elevations([(G.nodes[n]["x"], G.nodes[n]["y"]) for n in ids])
    for n, z in zip(ids, zs):
        G.nodes[n]["elevation"] = round(float(z), 1)
    for u, v, k, d in G.edges(keys=True, data=True):
        d["slope_pct"] = round(slope_pct(G.nodes[u]["elevation"], G.nodes[v]["elevation"], d["length"]), 2)
    return G
