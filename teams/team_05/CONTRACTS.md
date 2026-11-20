# Снегоплан — CONTRACTS

Snow-plow route optimisation, Petropavlovsk-Kamchatsky. Goal: minimise deadheading.
This file + `backend/core/models.py` (pydantic) + `frontend/src/api/types.ts` (TS mirror) are the
contracts. Change them only with an explicit note here; keep models.py and types.ts in sync.

## 0. Layout, running, imports
```
backend/  app/ (FastAPI)  core/ (models, graph, elevation)  solver/  sim/  scripts/  data/  tests/
frontend/ (React+Vite+TS+MapLibre)
```
* **Working dir for all backend commands = `backend/`** (it is the import root): `from core.models import ...`,
  `from solver.cost import ...`, `from tests.synthetic import ...`. Run: `uvicorn app.main:app --reload --port 8000`,
  tests: `python -m pytest` (pytest.ini sets pythonpath).
* `core`, `solver`, `sim` must NOT import fastapi/osmnx at module top (only networkx/numpy/pydantic/scipy)
  so they run in lean environments. `app/` and `scripts/` may use fastapi/osmnx.
* Windows-safe: pathlib only, read/write files as utf-8.
* Frontend: `npm install && npm run dev` (port 5173, proxies `/api`, `/ws` to :8000). Routes `/dispatcher`,
  `/driver?v=П-01`.

## 1. Conventions
* WGS84, **[lon, lat]** order everywhere (GeoJSON). MVP bbox: lon 158.60–158.68, lat 53.00–53.07
  (`core.graph.BBOX = (lon_min, lat_min, lon_max, lat_max)`).
* Units: metres (`*_m`), km (`*_km`), minutes (`*_min`), litres (`fuel_l`), percent slope (`slope_pct`, + = uphill
  in direction of the edge). Sim time `t` = float **seconds since scenario start** (scenario clock starts 06:00).
* Ids: node `int`; edge `"<u>-<v>-<key>"`; task `"T-0001"`; vehicle `"П-01"` (plow) / `"С-01"` (truck);
  event `"E-0001"`; proposal `"R-0001"`; plan `"P-0001"`.
* JSON fields snake_case; enums are lowercase strings (`StepKind.return_` serialises as `"return"`).
* **All user-facing text is Russian** (UI labels, `Event.text`, `reason_text`, street names). Code, ids, logs and
  comments in English. Vehicle display names: "Снегоуборщик П-01", "Самосвал С-01". Depot "База №1", dump
  "Снегоотвал №1". Sim clock shown "HH:MM". Distances "12,4 км", fuel "л", time "ч мин". Event text examples:
  `Поломка: П-03 остановился на улица Ленинская`, `Новая заявка T-0276: расчистить проспект Лесная`,
  `П-02 завершил участок T-0014`, `Применён новый план (R-0003)`.

## 2. Graph (core.graph) — `networkx.MultiDiGraph`
* graph attrs: `bbox`, `depot_node`, `dump_node`, `crs="EPSG:4326"`.
* node attrs: `x`=lon, `y`=lat (osmnx names), `elevation` (m), `kind` ("junction"|"depot"|"dump"), `name`.
* edge attrs (on `G[u][v][key]`): `edge_id`, `length` (m), `slope_pct`, `geometry` (**list of [lon,lat]**, oriented
  u→v, NOT shapely), `name`, `highway`, `oneway` (bool), `required` (bool), `street_id`, `reverse_edge_id`,
  `task_id`. Two-way street = 2 directed edges sharing `street_id`, `slope_pct` negated.
* **Task = undirected street segment** (`build_tasks`): `edge_ids` = [fwd, rev] for two-way, [e] for one-way.
  Servicing any listed edge counts one pass. `required=False` edges (service roads, yard) are only for deadhead.
* Implemented in `core/graph.py`: `make_edge_id`, `parse_edge_id`, `haversine_m`, `graph_from_models`,
  `graph_to_models`, `edge_model`, `edge_data`, `save_graph`, `load_graph`, `build_tasks`.
  **To implement (graph/data agent)**: `build_graph_from_osm`, `shortest_path_edges`, `edge_position`,
  `nearest_edge`, `edges_geojson`. `core/elevation.py`: `get_elevations`, `attach_elevation` (`slope_pct` helper done).
* Real data → `backend/data/graph.json` (format below). If missing, the app falls back to the synthetic city.

## 3. Solver (backend/solver)
```python
solve_plows(graph, tasks, vehicles, weights=None, start_positions=None, time_limit_s=5) -> Plan   # carp.py
baseline_plows(graph, tasks, vehicles, weights=None, start_positions=None) -> Plan                 # baseline.py
plan_trucks(graph, pickup_edge_ids, trucks, dump_node, weights=None, start_positions=None, time_limit_s=3) -> Plan  # trucks.py (phase 2)
compute_metrics(plan, weights=None, ref=None) -> Metrics                  # metrics.py  IMPLEMENTED
fill_plan_metrics(plan, baseline=None) -> Plan ; compare_metrics(baseline, optimized, weights) -> MetricsComparison  # IMPLEMENTED
cost.edge_cost / vehicle_edge_cost / unload_cost / objective             # cost.py      IMPLEMENTED
```
Rules every solver must follow:
* Input `tasks` may contain done/cancelled ones: plan only `pending|assigned` with `passes_done < passes`
  (replanning = pass the current task list + `start_positions`).
* Route = **edge-level contiguous `RouteStep` list** per vehicle id; deadhead paths are expanded one step per
  directed edge. `service` steps carry `task_id`; each plow route ends with `return` steps to the finish node = `cost.finish_node` (snow dump by default,
  `FINISH_AT="dump"|"depot"`, env SNEGOPLAN_FINISH_AT; may be empty if already there). `t_start/t_end/fuel_l/length_m` filled via `solver.cost` (times relative to
  `plan.plan_start_t`, default 0). Broken vehicles get no route. Vehicles never planned onto unreachable tasks;
  those ids go to `plan.unassigned_task_ids`.
* Metrics: `service_km`=Σ service steps, `deadhead_km`=Σ deadhead+return steps (trucks: empty running),
  `total_time_min`=makespan, `fuel_l`=Σ step fuel. Call `fill_plan_metrics`. Baseline plan: `solver="baseline"`.
* Objective weights default deadhead/time/fuel = 0.5/0.3/0.2. `objective()` normalises by baseline metrics when
  given, so baseline ≡ 1.0 and lower is better. Percent comparisons: `compare_metrics` (negative = better).
* Cost model (see `solver/cost.py` docstring): plow service 15 km/h, deadhead 35 km/h; uphill speed factor
  `max(0.35, 1 - K*slope)` (K: plow service .05, deadhead .025, truck loaded .06); fuel l/km: plow service .60,
  deadhead .38, truck empty .35, loaded .55, × slope term (+10%/% uphill for service). Directional (asymmetric).
* Deterministic for fixed input; respect `time_limit_s` (anytime heuristic: constructive + local search).

## 4. Simulator (backend/sim) — `SimEngine`
```python
SimEngine(graph, tasks, vehicles, config: AppConfig, solve_fn, baseline_fn)   # builds plan+baseline at t=0, paused
tick(dt_real_s)  set_speed(1|10|60)  start()  pause()  reset()
inject_breakdown(vehicle_id) -> Event
inject_request(edge_id=None, lon=None, lat=None, street_name=None, priority=2, passes=1) -> Event
mark_task_done(task_id, vehicle_id) -> Event          # driver button
pending_proposal (property) -> ReplanProposal|None
apply_proposal(proposal_id) -> Plan   reject_proposal(proposal_id)
replan(reason_event) -> ReplanProposal
snapshot() -> SimState     drain_events() -> list[Event]
```
* sim_dt = real_dt × speed; sub-step ≤ 5 sim-s. Vehicles move along edge `geometry` (interpolate by offset);
  `Position.edge_id/offset_m` always set while on an edge. Service steps auto-complete in sim (task → `done`,
  event `task_done`); `mark_task_done` does the same on driver POST (and teleports nothing: vehicle just
  proceeds). Task becomes `in_progress` when a plow starts its service step, `assigned` when a plan assigns it.
* Every event (`breakdown`, `new_request`) → `replan()` immediately → `ReplanProposal` (status pending) with
  `diffs`, `metrics_before/after` (remaining work), `new_plan`. If `config.demo_auto_apply`: `auto_apply_at =
  time.time() + auto_apply_delay_s` (wall clock) and `tick` applies it when due; otherwise dispatcher calls
  apply/reject via REST. A newer event supersedes (status `expired`) an unapplied proposal.
* Breakdown: vehicle stops in place, status `broken`, its unfinished tasks → `pending`; replan uses remaining
  non-broken plows with `start_positions` = their current positions. Request: creates/reopens a Task
  (`source="request"`, given priority), appended to `tasks`.
* Snapshot `vehicles[].route` is trimmed to the remaining steps (+ last 2 done) to keep the payload small.

## 5. Data files (backend/data) — all utf-8
* `graph.json`: `{"meta":{bbox,depot_node,dump_node,crs}, "nodes":[Node...], "edges":[Edge...]}` (models.Node / models.Edge dumps).
  Written/read by `core.graph.save_graph/load_graph`. Built by `scripts/build_graph.py` (osmnx `graph_from_bbox`
  type drive, + elevation, + slope, + street pairing).
* `basemap.geojson`: one GeoJSON FeatureCollection; each feature has `properties.layer` ∈ {`roads`,`water`,`parks`,
  `buildings`}; roads also `highway`, `name`; geometries LineString (roads) / Polygon|MultiPolygon (others),
  simplified, clipped to bbox. Built by `scripts/build_basemap.py` (osmnx features). Missing → empty layers.
* `elevation_cache.json`: `{"lon,lat" (5dp): metres}`; optional `dem.tif`. Depot ("База №1") and dump
  ("Снегоотвал №1") coordinates: defined in `app/config.py` (nearest graph nodes get `kind` depot/dump).

## 6. REST API (prefix `/api`, JSON, utf-8; errors `{"detail": "..."}` with 4xx/5xx)
| Method Path | Body | Response |
|---|---|---|
| GET `/health` | – | `{ok:true}` |
| GET `/basemap` | – | `Basemap` = `{bbox:[..4], layers:{roads,water,parks,buildings: FeatureCollection}}` |
| GET `/graph/edges` | – | FeatureCollection of edges, props `id,name,required,task_id,status,slope_pct,length_m` |
| GET `/state` | – | `SimState` |
| GET `/vehicles` | – | `Vehicle[]` |
| GET `/tasks?status=` | – | `Task[]` |
| GET `/plan` · `/plan/baseline` | – | `Plan` (current / baseline) |
| GET `/metrics` | – | `MetricsComparison` (baseline vs current plan) |
| POST `/plan/solve` | `SolveRequest` | `Plan` (re-optimise from current state; applied immediately, no proposal) |
| POST `/sim/control` | `{action:"start"\|"pause"\|"reset", speed?:1\|10\|60}` | `SimState` |
| POST `/events/breakdown` | `{vehicle_id}` | `Event` (404 unknown vehicle) |
| POST `/events/request` | `NewRequestBody` (edge_id \| lon+lat \| street_name) | `Event` |
| GET `/proposal` | – | `ReplanProposal \| null` |
| POST `/proposal/{id}/apply` · `/reject` | – | `Plan` · `{ok:true}` (409 if not pending) |
| POST `/tasks/{task_id}/complete` | `{vehicle_id}` | `Event` |
| GET `/driver/{vehicle_id}` | – | `DriverView` (current task, next ≤5 tasks, remaining route GeoJSON) |
| GET / PUT `/config` | `AppConfig` | `AppConfig` |

## 7. WebSocket `/ws`
Server → client, envelope `{"type": ..., "data": ...}`:
* `snapshot` → `SimState` — every `snapshot_interval_ms` (250 ms) while any client connected (even paused);
  sent immediately on connect.
* `event` → `Event` — once per new event.
* `proposal` → `ReplanProposal | null` — on creation, status change (null = none pending).
* `plan` → `Plan` — when a new plan is applied.
* `pong` → null — reply to client `{"type":"ping"}`.
Client → server: only `{"type":"ping"}`; all commands use REST. Frontend reconnects automatically
(`src/api/client.ts: connectWs`). No auth.

## 8. Test fixture — `backend/tests/synthetic.py` (IMPLEMENTED)
`build_synthetic_city(seed=42, rows=12, cols=13, n_plows=4, n_trucks=0) -> SyntheticCity(graph, nodes, edges,
tasks, vehicles, depot_node, dump_node, bbox)`: 156 nodes, 526 directed edges, 275 tasks, 48 one-way edges,
strongly connected, hill ~80 m in the centre, depot node 0 ("База №1"), dump node 155 ("Снегоотвал №1"),
street names Russian. Same structures as the real loader, so `solve_plows(city.graph, city.tasks, city.vehicles)`
and `SimEngine(city.graph, ...)` work identically on real data. Use `foundation` tests as examples
(`tests/test_foundation.py`).

## 9. Ownership / parallel work
* graph+data agent: `core/graph.py` stubs, `core/elevation.py`, `scripts/*`, basemap build.
* solver agent: `solver/carp.py`, `baseline.py`, (later) `trucks.py`; tests vs synthetic (CARP must beat baseline).
* sim agent: `sim/engine.py`, `sim/events.py`.
* app agent: `app/main.py`, `api.py`, `ws.py`, wiring + `sim_loop`.
* frontend agent: pages, MapLibre rendering from `/api/basemap` (no external tiles; style = background +
  our GeoJSON layers only), vehicles/tasks/route overlay, proposal card, driver page.

## 10. Addendum: logs and dump trucks
* Logs in `<repo>/logs/`: `backend.log` (app, REST, sim events, replan timings, WS, tracebacks), `prepare_data.log`,
  `frontend.log` (browser errors POSTed to `POST /api/client-log` `{kind,message,stack,url,ua}`). `core/logsetup.py`.
* `Metrics` gained `truck_empty_km`, `truck_loaded_km`, `truck_fuel_l` (live metrics only; plow metrics/baseline
  comparison exclude trucks). `solver/trucks.py`: `plan_truck_trips`, `select_pickup_tasks`, `plan_trucks`.
  Trucks are dispatched by the sim when the pickup task is done. Default fleet: `SNEGOPLAN_TRUCKS=2` (С-01, С-02).

### Vehicle.trail (WS snapshots)
`Vehicle.trail: [lon,lat][]` (default `[]`) - exact road polyline the vehicle travelled since the previous WS
broadcast snapshot (starts at the previous position, ends at the current one; <= 500 points; empty if it did not
move). Filled only in the single per-tick WS broadcast (`snapshot(consume_trail=True)`), then reset; REST/initial
snapshots and driver_view carry `[]`. Frontend animates markers along it and snaps (never lerps) when no path / gap > 30 m.
