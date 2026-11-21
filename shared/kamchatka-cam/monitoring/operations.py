from datetime import datetime, timedelta, timezone
from uuid import uuid4

from psycopg.types.json import Jsonb

from .config import LOCAL_TZ
from .scene import RULES, event_signals, russian_conclusion


def local_time(when):
    return when.astimezone(LOCAL_TZ).strftime("%d.%m.%Y %H:%M")


def enqueue(conn, settings, run_id, key, kind, subject, body, media_key=None):
    recipients = settings.recipients if settings.mode == "live" and run_id == "live" else ()
    if settings.mail_test and settings.mode == run_id == "preview":
        recipients = ("dispatch@example.test",)
    stage = "queued" if recipients else "logged"
    recipients = recipients or ("journal",)
    for recipient in recipients:
        conn.execute(
            """INSERT INTO outbox (run_id,key,kind,recipient,subject,body,media_key,stage)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (run_id, key, kind, recipient, subject, body, media_key, stage),
        )


def store_frame(conn, run_id, camera_id, captured, media, *, analyze=True):
    payload, width, height, digest, source_at, observed_at = captured
    camera = conn.execute(
        "SELECT * FROM cameras WHERE run_id=%s AND id=%s FOR UPDATE", (run_id, camera_id)
    ).fetchone()
    repeated = digest == camera["last_digest"]
    source_age_limit = timedelta(seconds=max(300, 2 * (camera["interval_seconds"] or 0)))
    old = source_at is not None and (
        source_at < observed_at - source_age_limit
        or camera["last_source_at"] is not None and source_at < camera["last_source_at"]
    )
    if repeated or old:
        conn.execute("UPDATE cameras SET status=%s,last_poll=%s WHERE run_id=%s AND id=%s",
                     ("stale" if old else "unchanged", observed_at, run_id, camera_id))
        return None
    frame_id = uuid4()
    key = media.put(run_id, payload)
    conn.execute(
        """INSERT INTO frames (id,run_id,camera_id,observed_at,source_at,digest,media_key,width,height)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (frame_id, run_id, camera_id, observed_at, source_at, digest, key, width, height),
    )
    if analyze:
        conn.execute("INSERT INTO jobs (frame_id) VALUES (%s)", (frame_id,))
    conn.execute(
        """UPDATE cameras SET status='available',last_poll=%s,last_frame=%s,last_digest=%s,
           last_source_at=COALESCE(%s,last_source_at) WHERE run_id=%s AND id=%s""",
        (observed_at, observed_at, digest, source_at, run_id, camera_id),
    )
    return frame_id


def record_observation(conn, settings, frame, features, duration_ms=None):
    camera = conn.execute(
        "SELECT * FROM cameras WHERE run_id=%s AND id=%s FOR UPDATE",
        (frame["run_id"], frame["camera_id"]),
    ).fetchone()
    if frame["run_id"] in {"live", "preview"} and not camera["road"]:
        features = {**features, "road_coverage": "uncertain"}
        if features.get("details"):
            features["details"] = {**features["details"], "snowdrift_on_road": "uncertain",
                                   "slush_on_road": "uncertain", "road_obstruction": "uncertain"}
        features["evidence"] = russian_conclusion(features)
    saved = conn.execute(
        """INSERT INTO observations (frame_id,scene,duration_ms) VALUES (%s,%s,%s)
           ON CONFLICT DO NOTHING RETURNING frame_id""",
        (frame["id"], Jsonb(features), duration_ms),
    ).fetchone()
    if not saved:
        return
    run_id, camera_id, observed_at = frame["run_id"], frame["camera_id"], frame["observed_at"]
    if run_id in {"live", "preview"} and datetime.now(timezone.utc) - observed_at > timedelta(seconds=settings.freshness):
        return
    rows = conn.execute(
        """SELECT f.observed_at,o.scene FROM frames f LEFT JOIN observations o ON o.frame_id=f.id
           WHERE f.run_id=%s AND f.camera_id=%s AND f.observed_at <= %s
           ORDER BY f.observed_at DESC,f.id DESC LIMIT 3""",
        (run_id, camera_id, observed_at),
    ).fetchall()
    for kind, severity in event_signals(rows, camera["road"], settings.freshness).items():
        active = conn.execute(
            """SELECT * FROM events WHERE run_id=%s AND camera_id=%s AND kind=%s AND ended_at IS NULL
               FOR UPDATE""", (run_id, camera_id, kind),
        ).fetchone()
        if active and (active["last_seen"] > observed_at or severity == "end"):
            if severity == "end" and active["last_seen"] <= observed_at:
                conn.execute("UPDATE events SET ended_at=%s WHERE id=%s", (observed_at, active["id"]))
            continue
        if severity == "end":
            continue
        escalation = active and active["severity"] == "watch" and severity == "urgent"
        level = "urgent" if severity == "urgent" or active and active["severity"] == "urgent" else "watch"
        reason = RULES[kind][level == "urgent"]
        if active:
            event_id = active["id"]
            conn.execute(
                "UPDATE events SET latest_frame=%s,last_seen=%s,severity=%s,reason=%s WHERE id=%s",
                (frame["id"], observed_at, level, reason, event_id),
            )
        else:
            event_id = uuid4()
            conn.execute(
                """INSERT INTO events (id,run_id,camera_id,kind,severity,reason,first_frame,latest_frame,opened_at,last_seen)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (event_id, run_id, camera_id, kind, level, reason, frame["id"], frame["id"], observed_at, observed_at),
            )
        if not active or escalation:
            subject = f'{"Срочная проверка" if level == "urgent" else "Наблюдение"} · {camera["name"]}'
            body = f'{camera["name"]}\n{local_time(observed_at)} по Камчатке\n{reason}\n\n{settings.base_url}/?event={event_id}'
            enqueue(conn, settings, run_id, f"event:{event_id}:{level}", "event", subject, body, frame["media_key"])


def warning_window(periods, now, threshold):
    eligible = []
    for period in periods:
        at = datetime.fromisoformat(period["at"])
        if period["horizon"] == "hourly":
            start, stop = at, at + timedelta(hours=1)
        elif period["horizon"] == "three_hourly":
            start, stop = at - timedelta(hours=3), at
        else:
            local = at.astimezone(LOCAL_TZ)
            start = local.replace(hour=0, minute=0, second=0, microsecond=0)
            stop = start + timedelta(days=1)
            # A daily period can cover tomorrow's hourly data as well as later hours.
            if stop <= now + timedelta(hours=48):
                continue
        if now < stop and start <= now + timedelta(days=6) and period["snow"] and (
            period["probability"] is not None and period["probability"] >= threshold
        ):
            eligible.append((start, stop, period))
    if not eligible:
        return None
    eligible.sort(key=lambda item: (max(item[0], now), item[2]["horizon"] != "hourly"))
    start, stop, first = eligible[0]
    matching = [first]
    for next_start, next_stop, period in eligible[1:]:
        if next_start > stop or period["horizon"] != first["horizon"]:
            break
        stop = max(stop, next_stop)
        matching.append(period)
    snow_values = [period["snow_mm"] for period in matching if period["snow_mm"] is not None]
    return {**first, "start": start.isoformat(), "end": stop.isoformat(),
            "probability": max(period["probability"] for period in matching),
            "snow_mm": sum(snow_values) if len(snow_values) == len(matching) else None}


def save_forecast(conn, settings, run_id, point, periods, now):
    conn.execute("INSERT INTO forecasts (run_id,point,fetched_at,periods) VALUES (%s,%s,%s,%s)",
                 (run_id, point, now, Jsonb(periods)))
    window = warning_window(periods, now, settings.snow_probability)
    conn.execute("INSERT INTO warnings (run_id,point) VALUES (%s,%s) ON CONFLICT DO NOTHING", (run_id, point))
    saved = conn.execute("SELECT * FROM warnings WHERE run_id=%s AND point=%s FOR UPDATE", (run_id, point)).fetchone()
    previous = saved["payload"]
    if window and previous and window["horizon"] == previous["horizon"]:
        shift = abs(datetime.fromisoformat(window["start"]) - datetime.fromisoformat(previous["start"]))
        end_shift = abs(datetime.fromisoformat(window["end"]) - datetime.fromisoformat(previous["end"]))
        if shift < timedelta(hours=2) and end_shift < timedelta(hours=2):
            return
    if window is None and previous is None:
        return
    revision = saved["revision"] + 1
    conn.execute("UPDATE warnings SET payload=%s,revision=%s WHERE run_id=%s AND point=%s",
                 (Jsonb(window) if window else None, revision, run_id, point))
    if window:
        subject = f'{"Уточнение прогноза" if previous else "Предупреждение о снегопаде"} · {point}'
        start, stop = (local_time(datetime.fromisoformat(window[key])) for key in ("start", "end"))
        snow = "нет данных" if window["snow_mm"] is None else f'{window["snow_mm"]:.1f} мм осадков'
        wind = "нет данных" if window["wind_mps"] is None else f'{window["wind_mps"]} м/с'
        direction = "" if window["wind_deg"] is None else f', {window["wind_deg"]}°'
        gust = "" if window["gust_mps"] is None else f', порывы до {window["gust_mps"]} м/с'
        body = (f'{point}\nОкно: {start} – {stop} по Камчатке\nВероятность осадков: {window["probability"]:.0%}'
                f'\nСнег по прогнозу: {snow}\nВетер: {wind}{direction}{gust}\n'
                f'Прогноз получен {local_time(now)}\n{settings.base_url}')
    else:
        if datetime.fromisoformat(previous["end"]) <= now:
            return
        subject = f"Предупреждение отозвано · {point}"
        body = f"Обновлённый прогноз {local_time(now)} больше не подтверждает предупреждение.\n{settings.base_url}"
    enqueue(conn, settings, run_id, f"forecast:{point}:{revision}", "forecast", subject, body)


def save_summary(conn, settings, run_id, now, force=False):
    local = now.astimezone(LOCAL_TZ)
    end = local.replace(hour=settings.summary_hour, minute=0, second=0, microsecond=0)
    if local < end and not force:
        return
    if force:
        end = local
    start = end - timedelta(days=1)
    events = conn.execute(
        """SELECT e.id,e.reason,e.state,e.opened_at,c.name FROM events e JOIN cameras c
           ON (c.run_id,c.id)=(e.run_id,e.camera_id) WHERE e.run_id=%s
           AND e.opened_at < %s AND (e.opened_at >= %s OR e.state IN ('new','acknowledged','deferred','en_route'))
           ORDER BY e.opened_at DESC""", (run_id, end, start),
    ).fetchall()
    actions = conn.execute(
        """SELECT a.state,a.note,a.created_at,c.name FROM actions a JOIN events e ON e.id=a.event_id
           JOIN cameras c ON (c.run_id,c.id)=(e.run_id,e.camera_id)
           WHERE e.run_id=%s AND a.created_at >= %s AND a.created_at < %s
           ORDER BY a.created_at DESC""", (run_id, start, end),
    ).fetchall()
    stale = conn.execute(
        """SELECT name FROM cameras WHERE run_id=%s AND
           (last_frame IS NULL OR last_frame < %s - GREATEST(%s, COALESCE(2*interval_seconds,0))*interval '1 second'
            OR status IN ('error','stale'))""",
        (run_id, now, settings.freshness),
    ).fetchall()
    warnings = conn.execute("SELECT point,payload FROM warnings WHERE run_id=%s AND payload IS NOT NULL", (run_id,)).fetchall()
    payload = {
        "start": start.isoformat(), "end": end.isoformat(),
        "events": [{**row, "id": str(row["id"]), "opened_at": row["opened_at"].isoformat()} for row in events],
        "actions": [{**row, "created_at": row["created_at"].isoformat()} for row in actions],
        "stale_cameras": [row["name"] for row in stale], "warnings": warnings,
    }
    if not conn.execute(
        "INSERT INTO summaries VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING day",
        (run_id, end.date(), Jsonb(payload)),
    ).fetchone():
        return
    states = {"new": "ожидает проверки", "acknowledged": "принято", "deferred": "отложено",
              "en_route": "техника в пути", "resolved": "закрыто"}
    body = [f"Период: {local_time(start)} – {local_time(end)} по Камчатке", ""]
    body.extend(f'{row["name"]} · {states[row["state"]]} · {row["reason"]}' for row in events)
    if not events:
        body.append("За период сигналов нет.")
    body.extend(f'{row["name"]} · {local_time(row["created_at"])} · {states[row["state"]]} · {row["note"]}' for row in actions)
    body.extend(f'Действует предупреждение по прогнозу: {row["point"]}' for row in warnings)
    if stale:
        body.append("Нет свежих кадров: " + ", ".join(row["name"] for row in stale))
    body.append(settings.base_url)
    enqueue(conn, settings, run_id, f"summary:{end.date()}", "summary", f"Утренняя сводка · {end:%d.%m.%Y}",
            "\n".join(body))
