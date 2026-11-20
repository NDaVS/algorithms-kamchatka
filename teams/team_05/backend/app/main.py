"""FastAPI app factory. Run from backend/:  uvicorn app.main:app --reload --port 8000"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import config
from core.logsetup import setup_logging

setup_logging("backend.log")
log = logging.getLogger("snegoplan.app")
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
LAYERS = ("roads", "water", "parks", "buildings")


def load_basemap(path: Path, bbox) -> dict:
    """Split data/basemap.geojson by properties.layer. Missing file -> empty layers."""
    layers = {k: {"type": "FeatureCollection", "features": []} for k in LAYERS}
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        for f in raw.get("features", []):
            lay = (f.get("properties") or {}).get("layer")
            if lay in layers:
                layers[lay]["features"].append(f)
    else:
        log.warning("basemap.geojson not found (%s): serving empty basemap", path)
    return {"bbox": list(bbox), "layers": layers, "pois": []}


def add_outside_dump(G, comp, lonlat) -> int:
    """Add a dump node at `lonlat` (outside the bbox) joined to the graph by a synthetic two-way trunk polyline
    starting at the northernmost major-road (trunk/primary) node. Returns the new dump node id."""
    import math
    from core.graph import haversine_m, make_edge_id
    x0, y0, x1, y1 = G.graph.get("bbox") or (158.60, 53.0, 158.68, 53.07)
    major = {"trunk", "primary", "trunk_link", "primary_link"}
    cand = set()
    for u, v, d in G.edges(data=True):
        hw = d.get("highway")
        if isinstance(hw, (list, tuple)):
            hw = hw[0] if hw else None
        if hw in major:
            cand.update((u, v))
    cand &= set(comp)
    cand = cand or set(comp)
    # nearest to the north bbox edge (directly "below" the dump)
    exit_n = min(cand, key=lambda n: haversine_m(G.nodes[n]["x"], G.nodes[n]["y"], lonlat[0], y1))
    ex, ey = G.nodes[exit_n]["x"], G.nodes[exit_n]["y"]
    new = max(G.nodes) + 1
    G.add_node(new, x=float(lonlat[0]), y=float(lonlat[1]), elevation=float(G.nodes[exit_n].get("elevation", 0.0)),
               kind="dump", name="Снегоотвал №1")
    n_mid = 5
    kx = math.cos(math.radians(ey))
    L = haversine_m(ex, ey, lonlat[0], lonlat[1])
    pts = [[ex, ey]]
    for i in range(1, n_mid + 1):
        t = i / (n_mid + 1)
        bend = 0.0006 * math.sin(math.pi * t) / kx  # gentle curve, ~45 m sideways
        pts.append([ex + (lonlat[0] - ex) * t + bend, ey + (lonlat[1] - ey) * t])
    pts.append([float(lonlat[0]), float(lonlat[1])])
    length = round(sum(haversine_m(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) for i in range(len(pts) - 1)), 1)
    ks = []
    for a, b, geom in ((exit_n, new, pts), (new, exit_n, pts[::-1])):
        k = G.add_edge(a, b, length=length, geometry=[list(p) for p in geom], name="Трасса к снегоотвалу",
                       highway="trunk", required=False, slope_pct=0.0, oneway=False, street_id=f"S-dump-{exit_n}-{new}",
                       reverse_edge_id=None, task_id=None)
        G[a][b][k]["edge_id"] = make_edge_id(a, b, k)
        ks.append((a, b, k))
    G[ks[0][0]][ks[0][1]][ks[0][2]]["reverse_edge_id"] = G[ks[1][0]][ks[1][1]][ks[1][2]]["edge_id"]
    G[ks[1][0]][ks[1][1]][ks[1][2]]["reverse_edge_id"] = G[ks[0][0]][ks[0][1]][ks[0][2]]["edge_id"]
    G.graph["dump_link"] = [list(p) for p in pts]
    log.info("DATA: outside dump node %s at %s linked to node %s (%.0f m road, straight %.0f m)", new, lonlat, exit_n, length, L)
    return new


def exclude_dead_ends(G, max_len_m: float | None = None) -> int:
    """Mark required=False on short residential/unclassified edges that touch a dead-end node (undirected
    degree 1). Both directions of the street are cleared. Returns the number of edges changed."""
    max_len_m = config.DEADEND_MAX_M if max_len_m is None else max_len_m
    nbrs = {n: (set(G.successors(n)) | set(G.predecessors(n))) - {n} for n in G.nodes}
    n_off = 0
    for u, v, d in G.edges(data=True):
        if not d.get("required", True):
            continue
        hw = d.get("highway")
        if isinstance(hw, (list, tuple)):
            hw = hw[0] if hw else None
        if hw not in config.DEADEND_HIGHWAYS or float(d.get("length", 0.0)) >= max_len_m:
            continue
        if len(nbrs[u]) <= 1 or len(nbrs[v]) <= 1:
            d["required"] = False
            n_off += 1
    return n_off


def prepare_real_graph(G):
    """Load-time fixes for graph.json: relocate depot/dump (nearest well-connected node to configured
    coords) and restrict required edges to named streets of plowable classes."""
    import networkx as nx
    from core.graph import haversine_m
    # 1. required filter
    before = sum(1 for _, _, d in G.edges(data=True) if d.get("required", True))
    for _, _, d in G.edges(data=True):
        nm = (d.get("name") or "").strip()
        hw = d.get("highway")
        if isinstance(hw, (list, tuple)):
            hw = hw[0] if hw else None
        if nm in config.UNNAMED_LABELS or hw not in config.REQUIRED_HIGHWAYS:
            d["required"] = False
    after = sum(1 for _, _, d in G.edges(data=True) if d.get("required", True))
    log.info("DATA: required edges %d -> %d (unnamed/service/living_street excluded)", before, after)
    # 1b. dead-end stubs / yards: short residential/unclassified edges ending at a degree-1 node
    ends = exclude_dead_ends(G)
    log.info("DATA: %d dead-end edges (<%.0f m, degree-1 endpoint) excluded from required", ends, config.DEADEND_MAX_M)
    # 2. depot / dump
    comp = max(nx.strongly_connected_components(G), key=len)
    x0, y0, x1, y1 = G.graph.get("bbox") or (158.60, 53.0, 158.68, 53.07)
    mx, my = (x1 - x0) * 0.08, (y1 - y0) * 0.08

    def pick(lonlat, exclude=()):
        cands = [n for n in comp if n not in exclude and x0 + mx < G.nodes[n]["x"] < x1 - mx
                 and y0 + my < G.nodes[n]["y"] < y1 - my and len(set(G.successors(n)) | set(G.predecessors(n))) >= 3]
        cands = cands or [n for n in comp if n not in exclude]
        return min(cands, key=lambda n: haversine_m(G.nodes[n]["x"], G.nodes[n]["y"], lonlat[0], lonlat[1]))

    old = (G.graph.get("depot_node"), G.graph.get("dump_node"))
    for n in old:
        if n in G.nodes:
            G.nodes[n].update(kind="junction", name=None)
    depot = pick(config.DEPOT_LONLAT)
    G.nodes[depot].update(kind="depot", name="База №1")
    if config.DUMP_OUTSIDE:
        dump = add_outside_dump(G, comp, config.DUMP_LONLAT)
    else:
        dump = pick(config.DUMP_LONLAT, exclude=(depot,))
        G.nodes[dump].update(kind="dump", name="Снегоотвал №1")
    G.graph.update(depot_node=depot, dump_node=dump)
    log.info("DATA: depot_node %s->%s dump_node %s->%s", old[0], depot, old[1], dump)


INITIAL_PLAN_CACHE = Path(__file__).resolve().parents[1] / "data" / "initial_plan.json"


def _plan_cache_key(cg, G, tasks, vehicles, cfg) -> str:
    import hashlib
    try:
        st = cg.GRAPH_PATH.stat()
        gf = (st.st_mtime_ns, st.st_size)
    except OSError:
        gf = None
    from solver import cost as _cost
    payload = {
        "solver_version": _cost.SOLVER_VERSION, "finish_at": _cost.FINISH_AT,
        "graph": gf, "n_tasks": len(tasks), "tasks": sorted(t.id for t in tasks),
        "fleet": [(v.id, v.kind, v.depot_node, getattr(v, "start_node", None)) for v in vehicles],
        "weights": cfg.weights.model_dump(mode="json"), "limit": cfg.solver_time_limit_s,
        "depot": G.graph.get("depot_node"), "dump": G.graph.get("dump_node"),
        "synthetic": bool(config.USE_SYNTHETIC),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _cached_solvers(key, solve_fn, baseline_fn):
    """Wrap solvers so the FIRST (initial, start_positions=None) call is served from / saved to initial_plan.json."""
    from core.models import Plan
    box: dict = {}
    try:
        if INITIAL_PLAN_CACHE.exists():
            d = json.loads(INITIAL_PLAN_CACHE.read_text(encoding="utf-8"))
            if d.get("key") == key:
                box["plan"] = Plan.model_validate(d["plan"])
                box["base"] = Plan.model_validate(d["baseline"]) if d.get("baseline") else None
                log.info("PLAN CACHE hit (%s): skipping initial solve", INITIAL_PLAN_CACHE.name)
            else:
                log.info("PLAN CACHE stale: re-solving")
    except Exception:
        log.exception("PLAN CACHE unreadable; re-solving")
        box.clear()
    hit = "plan" in box
    state = {"plan_used": False, "base_used": False}

    def solve(*a, **k):
        if not state["plan_used"] and k.get("start_positions") is None:
            state["plan_used"] = True
            if hit:
                return box["plan"].model_copy(deep=True)
            plan = solve_fn(*a, **k)
            box["plan"] = plan.model_copy(deep=True)
            return plan
        return solve_fn(*a, **k)

    def base(*a, **k):
        if not state["base_used"] and k.get("start_positions") is None:
            state["base_used"] = True
            if hit and box.get("base") is not None:
                return box["base"].model_copy(deep=True)
            b = baseline_fn(*a, **k)
            box["base"] = b.model_copy(deep=True)
            return b
        return baseline_fn(*a, **k)

    def save():
        if hit or "plan" not in box:
            return
        try:
            tmp = INITIAL_PLAN_CACHE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"key": key, "plan": box["plan"].model_dump(mode="json"),
                                       "baseline": box["base"].model_dump(mode="json") if box.get("base") else None},
                                      ensure_ascii=False), encoding="utf-8")
            tmp.replace(INITIAL_PLAN_CACHE)
            log.info("PLAN CACHE saved -> %s", INITIAL_PLAN_CACHE)
        except Exception:
            log.exception("PLAN CACHE save failed")
    return solve, base, save


def build_state(cfg):
    """Blocking: load graph (or synthetic fallback) and build the SimEngine."""
    from core import graph as cg
    from core.models import make_default_fleet
    from sim.engine import SimEngine
    from solver.baseline import baseline_plows
    from solver.carp import solve_plows

    t0 = time.perf_counter()
    if not config.USE_SYNTHETIC and cg.GRAPH_PATH.exists():
        G = cg.load_graph()
        prepare_real_graph(G)
        tasks = cg.build_tasks(G)
        depot = G.graph["depot_node"]
        vehicles = make_default_fleet(depot, cfg.n_plows, cfg.n_trucks)
        log.info("DATA: REAL graph loaded from %s", cg.GRAPH_PATH)
    else:
        msg = "WARNING: data/graph.json missing or SNEGOPLAN_SYNTHETIC=1 -> using SYNTHETIC city"
        print(msg, flush=True)
        log.warning(msg)
        log.warning("DATA: SYNTHETIC fallback (graph_path=%s exists=%s, SNEGOPLAN_SYNTHETIC=%s)",
                    cg.GRAPH_PATH, cg.GRAPH_PATH.exists(), config.USE_SYNTHETIC)
        from tests.synthetic import build_synthetic_city
        city = build_synthetic_city(n_plows=cfg.n_plows, n_trucks=cfg.n_trucks)
        G, tasks, vehicles = city.graph, city.tasks, city.vehicles
    log.info("DATA: nodes=%d edges=%d tasks=%d vehicles=%d (plows=%d trucks=%d) dump_node=%s",
             G.number_of_nodes(), G.number_of_edges(), len(tasks), len(vehicles),
             sum(1 for v in vehicles if v.kind == "plow"), sum(1 for v in vehicles if v.kind == "truck"),
             G.graph.get("dump_node"))
    solve_c, base_c, save_cache = _cached_solvers(_plan_cache_key(cg, G, tasks, vehicles, cfg), solve_plows, baseline_plows)
    sim = SimEngine(G, tasks, vehicles, cfg, solve_c, base_c)
    save_cache()
    log.info("Engine ready in %.1fs; initial plan %s", time.perf_counter() - t0, sim.plan.id)
    sim.async_replan = True
    bbox = G.graph.get("bbox") or cg.BBOX
    basemap = load_basemap(cg.BASEMAP_PATH, bbox)
    link = G.graph.get("dump_link")
    if link:  # in-memory only: draw the synthetic highway to the outside dump
        basemap["layers"]["roads"]["features"].append({
            "type": "Feature", "properties": {"layer": "roads", "highway": "trunk", "name": "Трасса к снегоотвалу"},
            "geometry": {"type": "LineString", "coordinates": link}})
    for key, label in (("depot_node", "База №1"), ("dump_node", "Снегоотвал №1")):
        n = G.graph.get(key)
        if n is not None and n in G.nodes:
            basemap["pois"].append({"kind": "depot" if key == "depot_node" else "dump", "name": label,
                                    "lon": G.nodes[n]["x"], "lat": G.nodes[n]["y"]})
    return G, sim, basemap


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from app.ws import sim_loop
        cfg = config.default_config()
        app.state.cfg = cfg
        log.info("=== Snegoplan backend starting; log file: %s ===", setup_logging("backend.log"))
        app.state.sim = None
        app.state.ready = asyncio.Event()
        app.state.startup_error = None

        async def _boot():
            try:
                G, sim, basemap = await asyncio.get_running_loop().run_in_executor(None, build_state, cfg)
            except Exception as e:
                log.exception("STARTUP FAILED while building state")
                app.state.startup_error = repr(e)
                return
            app.state.graph, app.state.basemap = G, basemap
            app.state.tasks = sim.tasks
            app.state.sim = sim
            app.state.ready.set()
            app.state.loop_task = asyncio.create_task(sim_loop(app))
            log.info("Backend READY")

        boot_task = asyncio.create_task(_boot())
        try:
            yield
        finally:
            log.info("Backend shutting down")
            boot_task.cancel()
            lt = getattr(app.state, "loop_task", None)
            if lt:
                lt.cancel()

    app = FastAPI(title="Снегоплан API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def log_requests(request, call_next):
        t0 = time.perf_counter()
        path = request.url.path
        if path.startswith("/api/") and path != "/api/health" and getattr(request.app.state, "sim", None) is None:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "starting", "status": "starting"}, status_code=503)
        noisy = request.method == "GET" or not path.startswith("/api/")
        try:
            resp = await call_next(request)
        except Exception:
            log.exception("REST %s %s -> unhandled exception", request.method, path)
            raise
        ms = (time.perf_counter() - t0) * 1000
        if path.startswith("/api/"):
            lvl = logging.WARNING if resp.status_code >= 400 else (logging.DEBUG if noisy else logging.INFO)
            log.log(lvl, "REST %s %s%s -> %d (%.0f ms)", request.method, path,
                    ("?" + request.url.query) if request.url.query else "", resp.status_code, ms)
        return resp

    from app.api import router as api_router
    from app.ws import router as ws_router
    app.include_router(api_router, prefix="/api")
    app.include_router(ws_router)

    if FRONTEND_DIST.exists():
        if (FRONTEND_DIST / "assets").exists():
            app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith(("api/", "ws")):
                raise HTTPException(404, "Not found")
            f = (FRONTEND_DIST / full_path).resolve()
            if full_path and f.is_file() and FRONTEND_DIST.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(FRONTEND_DIST / "index.html")
    return app


app = create_app()
