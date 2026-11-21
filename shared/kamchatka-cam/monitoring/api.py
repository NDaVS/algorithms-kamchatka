from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import hmac
from typing import Literal
from uuid import UUID

import jwt
import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .adapters import Media
from .config import ROOT, Settings
from .db import connect, migrate
from .dispatch import comparison, inspection_queue

TRANSITIONS = {
    "new": {"acknowledged", "deferred", "en_route", "resolved"},
    "acknowledged": {"deferred", "en_route", "resolved"},
    "deferred": {"acknowledged", "en_route", "resolved"},
    "en_route": {"deferred", "resolved"},
    "resolved": set(),
}


class Action(BaseModel):
    request_id: UUID
    expected_version: int = Field(ge=1)
    state: Literal["acknowledged", "deferred", "en_route", "resolved"]
    note: str = Field(default="", max_length=500)


class Feedback(BaseModel):
    verdict: Literal["confirmed", "false_alarm", "already_working"]


class WorkFrame(BaseModel):
    frame_id: UUID


def create_app(settings=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        settings.validate()
        migrate(settings)
        app.state.media = Media(settings)
        app.state.demo_media = Media(replace(settings, s3_bucket=""))
        app.state.jwks = jwt.PyJWKClient(settings.oidc_jwks, timeout=5) if settings.mode == "live" else None
        yield

    app = FastAPI(title="Камчатка · мониторинг", version="1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    def identity(request: Request, authorization: str = Header(default="")):
        if settings.mode != "live":
            return "local-demo", "operator"
        scheme, _, token = authorization.partition(" ")
        if not hmac.compare_digest(scheme.lower(), "bearer") or not token or len(token) > 16384:
            raise HTTPException(401, "Требуется вход", headers={"WWW-Authenticate": "Bearer"})
        try:
            key = request.app.state.jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=settings.oidc_audience,
                                issuer=settings.oidc_issuer, options={"require": ["exp", "iss", "sub", "aud"]})
        except jwt.exceptions.PyJWKClientConnectionError as exc:
            raise HTTPException(503, "Недоступна служба входа") from exc
        except jwt.PyJWTError as exc:
            raise HTTPException(401, "Недействительный токен") from exc
        actor = claims["sub"]
        if actor in settings.operators:
            return actor, "operator"
        if actor in settings.viewers:
            return actor, "viewer"
        raise HTTPException(403, "Нет доступа к мониторингу")

    def selected_run(conn, requested):
        if settings.mode == "live":
            if requested not in (None, "live"):
                raise HTTPException(404, "Запуск не найден")
            return "live"
        if settings.mode in {"demo", "preview"} and requested == "demo":
            return "demo"
        if settings.mode in {"demo", "preview"} and requested in (None, settings.mode):
            return settings.mode
        if requested is None:
            latest = conn.execute("SELECT id FROM runs WHERE mode='replay' ORDER BY created_at DESC LIMIT 1").fetchone()
            return latest["id"] if latest else ""
        found = conn.execute("SELECT 1 FROM runs WHERE id=%s AND mode='replay'", (requested,)).fetchone()
        if not found:
            raise HTTPException(404, "Запуск не найден")
        return requested

    def mark_evidence_age(events, run_id):
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.freshness)
        for event in events:
            event["evidence_stale"] = run_id in {"live", "preview"} and event["last_seen"] < cutoff

    @app.get("/healthz")
    def health():
        try:
            with connect(settings) as conn:
                conn.execute("SELECT 1")
        except psycopg.Error as exc:
            raise HTTPException(503, "Database unavailable") from exc
        return {"status": "ready"}

    @app.get("/api/v1/dashboard")
    def dashboard(run: str | None = None, user=Depends(identity)):
        with connect(settings) as conn:
            run_id = selected_run(conn, run)
            camera_rows = conn.execute(
                """SELECT c.id,c.name,c.area,c.road,c.coordinates,c.location_note,c.interval_seconds,c.status,c.last_poll,c.last_frame,
                          latest.id AS frame_id, latest.source_at, analyzed.scene,
                          analyzed.id AS analyzed_frame_id, analyzed.observed_at AS analyzed_frame_at
                   FROM cameras c LEFT JOIN LATERAL (
                       SELECT f.id,f.source_at FROM frames f WHERE f.run_id=c.run_id AND f.camera_id=c.id
                       ORDER BY f.observed_at DESC,f.id DESC LIMIT 1
                   ) latest ON true LEFT JOIN LATERAL (
                       SELECT f.id,f.observed_at,o.scene FROM frames f JOIN observations o ON o.frame_id=f.id
                       WHERE f.run_id=c.run_id AND f.camera_id=c.id ORDER BY f.observed_at DESC,f.id DESC LIMIT 1
                   ) analyzed ON true WHERE c.run_id=%s ORDER BY c.name""", (run_id,),
            ).fetchall()
            if run_id in {"live", "preview"}:
                now = datetime.now(timezone.utc)
                for row in camera_rows:
                    max_age = max(settings.freshness, 2 * (row["interval_seconds"] or 0))
                    cutoff = now - timedelta(seconds=max_age)
                    if row["last_frame"] and row["last_frame"] < cutoff:
                        row["status"] = "stale" if row["status"] != "error" else row["status"]
                    if row["analyzed_frame_at"] and row["analyzed_frame_at"] < cutoff:
                        row.update(scene=None, analyzed_frame_id=None, analyzed_frame_at=None)
            events = conn.execute(
                """SELECT e.id,e.kind,e.severity,e.reason,e.state,e.version,e.opened_at,e.last_seen,e.ended_at,
                          e.first_frame,e.latest_frame,c.name,c.area,c.road,c.road_priority,c.road_priority_reason,e.camera_id
                   FROM events e JOIN cameras c ON (c.run_id,c.id)=(e.run_id,e.camera_id) WHERE e.run_id=%s
                   ORDER BY (e.state='resolved'), (e.severity='urgent') DESC, e.last_seen DESC LIMIT 100""", (run_id,),
            ).fetchall()
            mark_evidence_age(events, run_id)
            forecasts = conn.execute(
                """SELECT DISTINCT ON (point) point,fetched_at,periods FROM forecasts
                   WHERE run_id=%s ORDER BY point,id DESC""", (run_id,),
            ).fetchall()
            warnings = conn.execute("SELECT point,payload FROM warnings WHERE run_id=%s AND payload IS NOT NULL",
                                    (run_id,)).fetchall()
            summaries = conn.execute("SELECT day,payload FROM summaries WHERE run_id=%s ORDER BY day DESC LIMIT 7",
                                     (run_id,)).fetchall()
            checks = conn.execute("SELECT name,status,observed_at FROM checks WHERE run_id=%s ORDER BY name",
                                  (run_id,)).fetchall()
            deliveries = conn.execute(
                "SELECT id,kind,subject,body,stage,attempts,error,created_at FROM outbox WHERE run_id=%s ORDER BY id DESC LIMIT 50",
                (run_id,),
            ).fetchall()
            feedback = conn.execute(
                """SELECT verdict,count(*) AS count FROM signal_feedback f JOIN events e ON e.id=f.event_id
                   WHERE e.run_id=%s GROUP BY verdict""", (run_id,),
            ).fetchall()
            queue = conn.execute(
                """SELECT count(*) AS pending,min(f.observed_at) AS oldest
                   FROM jobs j JOIN frames f ON f.id=j.frame_id
                   WHERE f.run_id=%s AND j.stage IN ('pending','running')""", (run_id,),
            ).fetchone()
        mode = run_id if run_id in {"live", "preview", "demo"} else "replay"
        return {"mode": mode, "run_id": run_id, "role": user[1], "cameras": camera_rows, "events": events,
                "forecasts": forecasts, "warnings": warnings, "summaries": summaries, "checks": checks, "deliveries": deliveries, "queue": queue,
                "inspection_queue": inspection_queue(events), "feedback_counts": {row["verdict"]: row["count"] for row in feedback}}

    @app.get("/api/v1/frames/{frame_id}")
    def frame(frame_id: UUID, request: Request, run: str | None = None, user=Depends(identity)):
        with connect(settings) as conn:
            run_id = selected_run(conn, run)
            row = conn.execute("SELECT media_key FROM frames WHERE run_id=%s AND id=%s", (run_id, frame_id)).fetchone()
        if not row:
            raise HTTPException(404, "Кадр не найден")
        try:
            storage = request.app.state.demo_media if run_id == "demo" else request.app.state.media
            image = storage.get(row["media_key"])
        except FileNotFoundError as exc:
            raise HTTPException(404, "Файл кадра недоступен") from exc
        return Response(image, media_type="image/jpeg")

    @app.get("/api/v1/events/{event_id}")
    def event_detail(event_id: UUID, run: str | None = None, user=Depends(identity)):
        with connect(settings) as conn:
            run_id = selected_run(conn, run)
            row = conn.execute(
                """SELECT e.id,e.kind,e.severity,e.reason,e.state,e.version,e.opened_at,e.last_seen,e.ended_at,
                          e.first_frame,e.latest_frame,c.name,c.area
                   FROM events e JOIN cameras c ON (c.run_id,c.id)=(e.run_id,e.camera_id)
                   WHERE e.run_id=%s AND e.id=%s""", (run_id, event_id),
            ).fetchone()
        if not row:
            raise HTTPException(404, "Событие не найдено")
        mark_evidence_age([row], run_id)
        return row

    @app.get("/api/v1/events/{event_id}/review")
    def review_event(event_id: UUID, run: str | None = None, user=Depends(identity)):
        with connect(settings) as conn:
            run_id = selected_run(conn, run)
            event = conn.execute("SELECT * FROM events WHERE run_id=%s AND id=%s", (run_id, event_id)).fetchone()
            if not event:
                raise HTTPException(404, "Событие не найдено")
            result = comparison(conn, event, datetime.now(timezone.utc), settings.freshness)
            result["feedback"] = conn.execute(
                "SELECT verdict,updated_at FROM signal_feedback WHERE event_id=%s AND actor=%s", (event_id, user[0]),
            ).fetchone()
            return result

    @app.put("/api/v1/events/{event_id}/feedback")
    def feedback_event(event_id: UUID, feedback: Feedback, run: str | None = None, user=Depends(identity)):
        if user[1] != "operator":
            raise HTTPException(403, "Оценка доступна оператору")
        with connect(settings) as conn:
            run_id = selected_run(conn, run)
            if not conn.execute("SELECT 1 FROM events WHERE run_id=%s AND id=%s", (run_id, event_id)).fetchone():
                raise HTTPException(404, "Событие не найдено")
            conn.execute(
                """INSERT INTO signal_feedback (event_id,actor,verdict) VALUES (%s,%s,%s)
                   ON CONFLICT (event_id,actor) DO UPDATE SET verdict=EXCLUDED.verdict,updated_at=now()
                   WHERE signal_feedback.verdict<>EXCLUDED.verdict""", (event_id, user[0], feedback.verdict),
            )
            return conn.execute("SELECT verdict,updated_at FROM signal_feedback WHERE event_id=%s AND actor=%s",
                                (event_id, user[0])).fetchone()

    @app.put("/api/v1/events/{event_id}/after-work")
    def pin_work_frame(event_id: UUID, frame: WorkFrame, run: str | None = None, user=Depends(identity)):
        if user[1] != "operator":
            raise HTTPException(403, "Действие доступно оператору")
        with connect(settings) as conn:
            run_id = selected_run(conn, run)
            event = conn.execute(
                "SELECT * FROM events WHERE run_id=%s AND id=%s FOR UPDATE", (run_id, event_id),
            ).fetchone()
            if not event:
                raise HTTPException(404, "Событие не найдено")
            now = datetime.now(timezone.utc)
            result = comparison(conn, event, now, settings.freshness)
            if event["after_work_frame"] == frame.frame_id:
                return result["after"]
            if not result["candidate"] or result["candidate"]["id"] != frame.frame_id:
                raise HTTPException(409, "Кадр изменился или устарел. Проверьте свежий кадр и повторите")
            conn.execute(
                "UPDATE events SET after_work_frame=%s,after_work_actor=%s,after_work_at=%s WHERE id=%s",
                (frame.frame_id, user[0], now, event_id),
            )
            return {**result["candidate"], "actor": user[0]}

    @app.get("/api/v1/events/{event_id}/actions")
    def actions(event_id: UUID, run: str | None = None, user=Depends(identity)):
        with connect(settings) as conn:
            run_id = selected_run(conn, run)
            if not conn.execute("SELECT 1 FROM events WHERE run_id=%s AND id=%s", (run_id, event_id)).fetchone():
                raise HTTPException(404, "Событие не найдено")
            return conn.execute(
                "SELECT request_id,actor,state,note,created_at FROM actions WHERE event_id=%s ORDER BY created_at DESC",
                (event_id,),
            ).fetchall()

    @app.post("/api/v1/events/{event_id}/actions")
    def act(event_id: UUID, action: Action, run: str | None = None, user=Depends(identity)):
        if user[1] != "operator":
            raise HTTPException(403, "Действие доступно оператору")
        note = action.note.strip()
        if action.state in {"resolved", "deferred"} and not note:
            raise HTTPException(422, "Укажи причину")
        with connect(settings) as conn:
            run_id = selected_run(conn, run)
            row = conn.execute("SELECT * FROM events WHERE run_id=%s AND id=%s FOR UPDATE", (run_id, event_id)).fetchone()
            if not row:
                raise HTTPException(404, "Событие не найдено")
            saved = conn.execute(
                "SELECT actor,state,note FROM actions WHERE event_id=%s AND request_id=%s",
                (event_id, action.request_id),
            ).fetchone()
            if saved:
                if saved != {"actor": user[0], "state": action.state, "note": note}:
                    raise HTTPException(409, "Ключ запроса уже использован")
                return {"state": row["state"], "version": row["version"]}
            if row["version"] != action.expected_version or action.state not in TRANSITIONS[row["state"]]:
                raise HTTPException(409, "Состояние изменилось. Обнови карточку")
            conn.execute("INSERT INTO actions (event_id,request_id,actor,state,note) VALUES (%s,%s,%s,%s,%s)",
                         (event_id, action.request_id, user[0], action.state, note))
            return conn.execute("UPDATE events SET state=%s,version=version+1 WHERE id=%s RETURNING state,version",
                                (action.state, event_id)).fetchone()

    dist = ROOT / "web" / "dist"
    if dist.is_dir():
        @app.get("/demo", include_in_schema=False)
        def demo_page():
            if settings.mode not in {"demo", "preview"}:
                raise HTTPException(404)
            return FileResponse(dist / "index.html")

        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
