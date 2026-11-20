"""WebSocket hub + sim loop. Protocol: CONTRACTS.md section 7."""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
log = logging.getLogger("snegoplan.ws")


class Hub:
    """Tracks connected sockets; broadcast(type, data) sends {"type":..., "data":...} JSON."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    @property
    def has_clients(self) -> bool:
        return bool(self.clients)

    async def connect(self, ws: WebSocket, accepted: bool = False) -> None:
        if not accepted:
            await ws.accept()
        self.clients.add(ws)
        log.info("WS connect %s (clients=%d)", getattr(ws, "client", None), len(self.clients))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.clients:
            log.info("WS disconnect %s (clients=%d)", getattr(ws, "client", None), len(self.clients) - 1)
        self.clients.discard(ws)

    async def broadcast(self, type_: str, data) -> None:
        if not self.clients:
            return
        text = json.dumps({"type": type_, "data": data}, ensure_ascii=False)
        clients = list(self.clients)
        results = await asyncio.gather(*(c.send_text(text) for c in clients), return_exceptions=True)
        for c, r in zip(clients, results):
            if isinstance(r, Exception):
                log.warning("WS send failed (%r); dropping client", r)
                self.disconnect(c)


hub = Hub()


def _log_future(fut) -> None:
    try:
        exc = fut.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        log.error("executor replan crashed", exc_info=exc)


def _dump(model) -> dict | None:
    return None if model is None else model.model_dump(mode="json")


async def sim_loop(app) -> None:
    """Every ~100 ms engine.tick(real dt); every snapshot_interval_ms broadcast a snapshot; broadcast
    new events, proposal changes and applied plans. Replans run in a thread executor."""
    eng = app.state.sim
    loop = asyncio.get_running_loop()
    last = last_snap = time.monotonic()
    pv, plv = eng.proposal_version, eng.plan_version
    replan_fut: asyncio.Future | None = None
    while True:
        await asyncio.sleep(0.1)
        try:
            now = time.monotonic()
            dt, last = min(now - last, 1.0), now
            eng.tick(dt)
            if eng.has_pending_replans and (replan_fut is None or replan_fut.done()):
                replan_fut = loop.run_in_executor(None, eng.run_pending_replans)
                replan_fut.add_done_callback(_log_future)
            for e in eng.drain_events():
                await hub.broadcast("event", _dump(e))
            if eng.plan_version != plv:
                plv = eng.plan_version
                await hub.broadcast("plan", _dump(eng.plan))
            if eng.proposal_version != pv:
                pv = eng.proposal_version
                await hub.broadcast("proposal", _dump(eng.pending_proposal))
            if hub.has_clients and (now - last_snap) * 1000 >= app.state.cfg.snapshot_interval_ms:
                last_snap = now
                await hub.broadcast("snapshot", _dump(eng.snapshot(consume_trail=True)))
            elif not hub.has_clients:
                eng.clear_trails()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("sim_loop iteration failed")


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while getattr(ws.app.state, "sim", None) is None:
            await ws.send_text(json.dumps({"type": "status", "data": {"status": "starting"}}))
            try:
                await asyncio.wait_for(ws.app.state.ready.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
    except Exception:
        return
    eng = ws.app.state.sim
    await hub.connect(ws, accepted=True)
    try:
        await ws.send_text(json.dumps({"type": "snapshot", "data": _dump(eng.snapshot())}, ensure_ascii=False))
        p = eng.pending_proposal
        if p is not None:
            await ws.send_text(json.dumps({"type": "proposal", "data": _dump(p)}, ensure_ascii=False))
        while True:
            msg = await ws.receive_json()
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong", "data": None}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        log.exception("ws endpoint error")
    finally:
        hub.disconnect(ws)
