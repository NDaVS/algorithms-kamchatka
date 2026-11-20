"""REST routes (thin). Contract: CONTRACTS.md section 6. Engine/graph live in request.app.state.
Handlers are plain `def` (run in the threadpool); the engine is internally locked."""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from core import graph as cg
from core.models import (AppConfig, BreakdownRequest, CompleteTaskRequest, DriverView, Event, MetricsComparison,
                         NewRequestBody, Plan, ProblemRequest, ReplanProposal, SimControlRequest, SimState, SolveRequest, Task,
                         TaskStatus, Vehicle)
from sim.engine import SimConflict, SimEngine, SimNotFound

router = APIRouter()
log = logging.getLogger("snegoplan.api")


def _sim(request: Request) -> SimEngine:
    return request.app.state.sim


def _call(fn, *a, **k):
    """Map engine exceptions to HTTP errors."""
    try:
        return fn(*a, **k)
    except SimNotFound as e:
        log.warning("command not found (404): %s", e)
        raise HTTPException(404, str(e.args[0] if e.args else e)) from e
    except SimConflict as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        log.warning("command rejected (422): %s", e)
        raise HTTPException(422, str(e)) from e
    except Exception:
        log.exception("command failed: %s", getattr(fn, "__name__", fn))
        raise


@router.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@router.get("/basemap")
def basemap(request: Request) -> dict[str, Any]:
    return request.app.state.basemap


@router.get("/graph/edges")
def graph_edges(request: Request) -> dict[str, Any]:
    sim = _sim(request)
    return cg.edges_geojson(sim.graph, sim.tasks_list())


@router.get("/state", response_model=SimState)
def state(request: Request):
    return _sim(request).snapshot()


@router.get("/vehicles", response_model=list[Vehicle])
def vehicles(request: Request):
    return _sim(request).vehicles_list()


@router.get("/tasks", response_model=list[Task])
def tasks(request: Request, status: str | None = None):
    if status is not None and status not in {s.value for s in TaskStatus}:
        raise HTTPException(422, f"unknown status {status}")
    return _sim(request).tasks_list(status)


@router.get("/plan", response_model=Plan)
def plan(request: Request):
    return _sim(request).plan


@router.get("/plan/baseline", response_model=Plan)
def plan_baseline(request: Request):
    b = _sim(request).baseline
    if b is None:
        raise HTTPException(404, "baseline plan unavailable")
    return b


@router.get("/metrics", response_model=MetricsComparison)
def metrics(request: Request):
    c = _sim(request).comparison()
    if c is None:
        raise HTTPException(404, "baseline plan unavailable")
    return c


@router.post("/plan/solve", response_model=Plan)
def solve(body: SolveRequest, request: Request):
    return _call(_sim(request).resolve, body.weights, body.time_limit_s)


@router.post("/sim/control", response_model=SimState)
def sim_control(body: SimControlRequest, request: Request):
    sim = _sim(request)

    def go():
        if body.action == "reset":
            sim.reset()
        if body.speed is not None:
            sim.set_speed(body.speed)
        if body.action == "start":
            sim.start()
        elif body.action == "pause":
            sim.pause()
        return sim.snapshot()
    return _call(go)


@router.post("/events/breakdown", response_model=Event)
def breakdown(body: BreakdownRequest, request: Request):
    return _call(_sim(request).inject_breakdown, body.vehicle_id)


@router.post("/events/request", response_model=Event)
def new_request(body: NewRequestBody, request: Request):
    return _call(_sim(request).inject_request, body.edge_id, body.lon, body.lat, body.street_name,
                 body.priority, body.passes)


@router.post("/events/problem", response_model=Event)
def problem(body: ProblemRequest, request: Request):
    return _call(_sim(request).report_problem, body.vehicle_id, body.kind, body.comment)


@router.get("/proposal", response_model=ReplanProposal | None)
def proposal(request: Request):
    return _sim(request).pending_proposal


@router.post("/proposal/{proposal_id}/apply", response_model=Plan)
def proposal_apply(proposal_id: str, request: Request):
    return _call(_sim(request).apply_proposal, proposal_id)


@router.post("/proposal/{proposal_id}/reject")
def proposal_reject(proposal_id: str, request: Request) -> dict[str, bool]:
    _call(_sim(request).reject_proposal, proposal_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/complete", response_model=Event)
def task_complete(task_id: str, body: CompleteTaskRequest, request: Request):
    return _call(_sim(request).mark_task_done, task_id, body.vehicle_id)


@router.get("/driver/{vehicle_id}", response_model=DriverView)
def driver(vehicle_id: str, request: Request):
    return _call(_sim(request).driver_view, vehicle_id)


@router.get("/config", response_model=AppConfig)
def get_config(request: Request):
    return request.app.state.cfg


@router.put("/config", response_model=AppConfig)
def put_config(body: AppConfig, request: Request):
    _call(_sim(request).set_config, body)
    request.app.state.cfg = body
    return body


@router.post("/client-log")
async def client_log(request: Request) -> dict[str, bool]:
    """Frontend error sink -> logs/frontend.log (never fails the client)."""
    from core.logsetup import frontend_logger
    try:
        raw = (await request.body())[:20000].decode("utf-8", "replace")
        try:
            d = json.loads(raw)
        except ValueError:
            d = {"message": raw}
        if not isinstance(d, dict):
            d = {"message": str(d)}
        frontend_logger().error("[%s] %s | url=%s | ua=%s\n%s", d.get("kind", "error"), d.get("message"),
                                d.get("url"), d.get("ua"), d.get("stack") or "")
    except Exception:  # noqa: BLE001
        log.exception("client-log failed")
    return {"ok": True}
