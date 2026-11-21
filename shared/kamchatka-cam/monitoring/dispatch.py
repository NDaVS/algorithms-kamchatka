from datetime import timedelta


BASELINE_STATES = {
    "snowfall": ("falling_snow", ["no"]),
    "coverage": ("road_coverage", ["clear", "partial"]),
    "drift": ("large_snowdrifts", ["no"]),
}
SIGNAL_REASONS = {
    "snowfall": "Камера зафиксировала снегопад",
    "coverage": "Снег устойчиво покрывает большую часть видимой дороги",
    "drift": "В зоне дороги виден крупный снежный вал",
}


def inspection_queue(events):
    by_camera = {}
    for event in events:
        if not event["road"] or event["state"] not in {"new", "acknowledged"}:
            continue
        if event["ended_at"] or event["evidence_stale"]:
            continue
        order = (event["severity"] == "urgent", event["road_priority"], event["last_seen"])
        current = by_camera.get(event["camera_id"])
        if current is None or current[0] < order:
            by_camera[event["camera_id"]] = (order, event)
    entries = []
    for _, event in sorted(by_camera.values(), key=lambda item: item[0], reverse=True):
        factors = [event["road_priority_reason"] or "Дорожный участок",
                   SIGNAL_REASONS.get(event["kind"], event["reason"]), "Есть свежее подтверждение"]
        entries.append({"event_id": event["id"], "name": event["name"], "area": event["area"],
                        "severity": event["severity"], "last_seen": event["last_seen"], "factors": factors})
    return entries


def comparison(conn, event, now, freshness):
    frames = conn.execute(
        """SELECT id,observed_at,width,height,media_key FROM frames WHERE id IN (%s,%s)""",
        (event["latest_frame"], event["after_work_frame"]),
    ).fetchall()
    by_id = {row["id"]: row for row in frames}
    current = by_id.get(event["latest_frame"])
    after = by_id.get(event["after_work_frame"])
    baseline = None
    if current and event["kind"] in BASELINE_STATES:
        field, states = BASELINE_STATES[event["kind"]]
        baseline = conn.execute(
            """SELECT f.id,f.observed_at FROM frames f JOIN observations o ON o.frame_id=f.id
               WHERE f.run_id=%s AND f.camera_id=%s AND f.observed_at < %s
               AND f.observed_at >= %s AND f.width=%s AND f.height=%s AND f.media_key<>%s
               AND o.scene->>'format_valid'='true' AND o.scene->>'view_quality' IN ('good','limited')
               AND o.scene->>%s = ANY(%s)
               ORDER BY f.observed_at DESC,f.id DESC LIMIT 1""",
            (event["run_id"], event["camera_id"], event["opened_at"], event["opened_at"] - timedelta(days=1),
             current["width"], current["height"], current["media_key"], field, states),
        ).fetchone()
    candidate = None
    if current and event["state"] in {"en_route", "resolved"}:
        earliest = conn.execute(
            """SELECT max(created_at) AS latest FROM actions
               WHERE event_id=%s AND state='en_route'""", (event["id"],),
        ).fetchone()["latest"] or event["opened_at"]
        candidate = conn.execute(
            """SELECT f.id,f.observed_at FROM frames f JOIN cameras c
               ON (c.run_id,c.id)=(f.run_id,f.camera_id) WHERE f.run_id=%s AND f.camera_id=%s
               AND f.observed_at > %s AND f.observed_at >= %s AND f.width=%s AND f.height=%s
               AND f.media_key<>%s AND c.status IN ('available','unchanged')
               ORDER BY f.observed_at DESC,f.id DESC LIMIT 1""",
            (event["run_id"], event["camera_id"], earliest, now - timedelta(seconds=freshness),
             current["width"], current["height"], current["media_key"]),
        ).fetchone()
    return {
        "before": baseline, "current": {"id": current["id"], "observed_at": current["observed_at"]} if current else None,
        "after": {"id": after["id"], "observed_at": after["observed_at"], "actor": event["after_work_actor"]} if after else None,
        "candidate": candidate,
    }
