import json
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from aiosmtpd.controller import Controller
from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest

from fixtures.prepare_demo import build_manifest
from monitoring.adapters import Media, forecast
from monitoring.api import create_app
from monitoring.config import Settings
from monitoring.db import connect, migrate, prepare_run
from monitoring.operations import enqueue, save_forecast, save_summary, store_frame
from monitoring.replay import load_replay
from monitoring.scene import event_signals
from monitoring.worker import analysis_tick, mail_tick


def model_fixture_rows():
    return build_manifest()["frames"][:4]


def test_four_control_labels():
    first, second, heavy, path = [sample["scene"] for sample in model_fixture_rows()]
    at = datetime.now(timezone.utc)

    def observed(scene, count=3, step=20):
        return [{"observed_at": at - timedelta(seconds=i * step), "scene": scene} for i in range(count)]

    assert event_signals(observed(first), True, 90) == {"snowfall": "end", "coverage": "end", "drift": "end"}
    assert event_signals(observed(second), False, 90) == {"snowfall": "end"}
    assert event_signals(observed(heavy, 1), True, 90) == {}
    assert event_signals(observed(heavy, 2), True, 90) == {"snowfall": "urgent"}
    assert event_signals(observed(heavy, 2, 91), True, 90) == {}
    assert event_signals(observed(path), False, 90) == {"snowfall": "watch"}
    assert event_signals(observed(heavy), False, 90) == {"snowfall": "watch"}
    invalid = {**heavy, "format_valid": False}
    assert event_signals([*observed(heavy, 1), *observed(invalid, 1)], True, 90) == {}


def test_openweather_response():
    at = 1795194000
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={
        "hourly": [{"dt": at, "temp": -1, "pop": .8, "weather": [{"id": 601}], "snow": {"1h": 1.4}}],
        "daily": [{"dt": at, "temp": {"day": 0}, "weather": [{"id": 803}]}],
    }))) as client:
        hour, day = forecast(client, "test", 53, 158)
    assert hour["snow"] is True and hour["snow_mm"] == 1.4
    assert day["snow"] is False and day["probability"] is None and day["snow_mm"] is None


@pytest.fixture
def settings(tmp_path):
    database = os.getenv("TEST_DATABASE_URL")
    if not database:
        pytest.skip("Set TEST_DATABASE_URL to a temporary PostgreSQL database")
    result = Settings(database=database, media_dir=tmp_path / "media", mode="replay", freshness=90)
    migrate(result)
    return result


def test_replay_api_actions_and_isolation(settings, tmp_path):
    run = "test-" + uuid4().hex
    manifest = build_manifest()
    manifest["frames"] = manifest["frames"][:4] + [{
        **manifest["frames"][2], "at": "2026-11-21T03:18:00+12:00", "file": "5.jpg",
    }]
    for i in range(1, 6):
        Image.new("RGB", (40, 25), (i * 20, 40, 80)).save(tmp_path / f"{i}.jpg")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    load_replay(settings, run, path)
    load_replay(settings, run, path)
    with TestClient(create_app(settings)) as client:
        query = {"run": run}
        response = client.get("/api/v1/dashboard", params=query)
        assert response.status_code == 200, response.text
        dashboard = response.json()
        event, = dashboard["events"]
        assert event["severity"] == "urgent"
        assert len(dashboard["cameras"]) == 4
        assert {item["kind"] for item in dashboard["deliveries"]} == {"event", "forecast", "summary"}
        assert {item["stage"] for item in dashboard["deliveries"]} == {"logged"}
        image = client.get(f'/api/v1/frames/{event["latest_frame"]}', params=query)
        assert image.status_code == 200 and image.content.startswith(b"\xff\xd8")
        assert client.get("/api/v1/dashboard", params={"run": "live"}).status_code == 404
        endpoint = f'/api/v1/events/{event["id"]}/actions'
        action = {"request_id": str(uuid4()), "expected_version": event["version"], "state": "en_route"}
        assert client.post(endpoint, params=query, json=action).json() == {"state": "en_route", "version": 2}
        assert client.post(endpoint, params=query, json=action).status_code == 200
        assert client.post(endpoint, params=query, json={**action, "request_id": str(uuid4())}).status_code == 409
        assert len(client.get(endpoint, params=query).json()) == 1


def test_forecast_update_withdrawal_and_summary_schedule(settings):
    run = "test-" + uuid4().hex
    now = datetime.fromisoformat("2026-11-21T07:22:00+12:00")
    period = build_manifest()["forecast"][0]["periods"][0]
    with connect(settings) as conn:
        prepare_run(conn, run, "replay", [])
        save_forecast(conn, settings, run, "Елизово", [period], now)
        save_forecast(conn, settings, run, "Елизово", [period], now + timedelta(minutes=30))
        shifted = {**period, "at": (datetime.fromisoformat(period["at"]) + timedelta(hours=3)).isoformat()}
        save_forecast(conn, settings, run, "Елизово", [shifted], now + timedelta(hours=1))
        save_forecast(conn, settings, run, "Елизово", [{**shifted, "snow": False}], now + timedelta(hours=2))
        save_summary(conn, settings, run, now - timedelta(hours=1))
        assert conn.execute("SELECT count(*) AS n FROM summaries WHERE run_id=%s", (run,)).fetchone()["n"] == 0
        save_summary(conn, settings, run, now)
        save_summary(conn, settings, run, now + timedelta(minutes=10))
        rows = conn.execute("SELECT kind,subject FROM outbox WHERE run_id=%s ORDER BY id", (run,)).fetchall()
    assert len(rows) == 4
    assert [row["subject"].split(" · ")[0] for row in rows] == [
        "Предупреждение о снегопаде", "Уточнение прогноза", "Предупреждение отозвано", "Утренняя сводка",
    ]


def test_worker_processes_frame_and_retryable_model_failure(settings):
    run = "test-" + uuid4().hex
    sample = model_fixture_rows()[2]["scene"]
    at = datetime.now(timezone.utc)
    media = Media(settings)
    with connect(settings) as conn:
        prepare_run(conn, run, "replay", [dict(id="street", name="Улица", area="Демо", road=True)])
        frame_id = store_frame(conn, run, "street", (b"frame bytes", 3, 2, "digest", None, at), media)
        assert store_frame(conn, run, "street", (b"frame bytes", 3, 2, "digest", None, at), media) is None

    requests = []

    def model(req):
        requests.append(req)
        assert req.url.path == "/v1/scene"
        assert b'name="image"' in req.read()
        return httpx.Response(503) if len(requests) == 1 else httpx.Response(200, json=sample)

    from dataclasses import replace
    config = replace(settings, model_url="https://model.invalid")
    with httpx.Client(transport=httpx.MockTransport(model)) as client:
        assert analysis_tick(config, run, media, client)
        with connect(settings) as conn:
            assert conn.execute("SELECT stage FROM jobs WHERE frame_id=%s", (frame_id,)).fetchone()["stage"] == "pending"
            conn.execute("UPDATE jobs SET available_at=now() WHERE frame_id=%s", (frame_id,))
        assert analysis_tick(config, run, media, client)
    with connect(settings) as conn:
        observation = conn.execute("SELECT scene FROM observations WHERE frame_id=%s", (frame_id,)).fetchone()
    assert observation["scene"]["intensity"] == "heavy"


def test_smtp_with_attachment_and_safe_retry_policy(settings):
    from dataclasses import replace
    from email import policy
    from email.parser import BytesParser
    from monitoring.adapters import AmbiguousDelivery

    messages = []

    class Sink:
        async def handle_DATA(self, server, session, envelope):
            messages.append(BytesParser(policy=policy.default).parsebytes(envelope.original_content))
            return "250 OK"

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    controller = Controller(Sink(), hostname="127.0.0.1", port=port)
    controller.start()
    try:
        config = replace(settings, mode="live", recipients=("operator@example.test",), mail_host="127.0.0.1",
                         mail_port=port, mail_security="none")
        media = Media(settings)
        run = "test-" + uuid4().hex
        key = media.put(run, b"attachment")
        with connect(settings) as conn:
            prepare_run(conn, run, "replay", [])
            enqueue(conn, settings, run, "a", "event", "Снегопад", "Тело", key)
            enqueue(conn, settings, run, "a", "event", "Снегопад", "Тело", key)
            enqueue(conn, settings, run, "b", "forecast", "Прогноз", "Тело")
            conn.execute("UPDATE outbox SET stage='queued',recipient=%s WHERE run_id=%s", (config.recipients[0], run))
        assert mail_tick(config, run, media)
        assert len(messages) == 1 and messages[0]["Subject"] == "Снегопад"
        assert next(messages[0].iter_attachments()).get_payload(decode=True) == b"attachment"

        def ambiguous(*args):
            raise AmbiguousDelivery

        assert mail_tick(config, run, media, transport=ambiguous)
        assert not mail_tick(config, run, media)
        with connect(settings) as conn:
            rows = conn.execute("SELECT stage FROM outbox WHERE run_id=%s ORDER BY id", (run,)).fetchall()
        assert [row["stage"] for row in rows] == ["sent", "ambiguous"]
    finally:
        controller.stop()


def test_oidc_validates_signature_and_roles(settings, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    config = replace(settings, mode="live", model_url="https://gpu.example.test",
                     base_url="https://portal.example.test", oidc_issuer="https://login.example.test",
                     oidc_jwks="https://login.example.test/keys", oidc_audience="monitor",
                     operators=("operator",), viewers=("viewer",))
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(jwt.PyJWKClient, "get_signing_key_from_jwt",
                        lambda self, token: SimpleNamespace(key=private_key.public_key()))

    def token(subject, audience="monitor"):
        return jwt.encode({"sub": subject, "aud": audience, "iss": config.oidc_issuer,
                           "exp": datetime.now(timezone.utc) + timedelta(minutes=2)},
                          private_key, algorithm="RS256")

    with TestClient(create_app(config)) as client:
        def headers(subject, audience="monitor"):
            return {"Authorization": "Bearer " + token(subject, audience)}

        assert client.get("/api/v1/dashboard").status_code == 401
        assert client.get("/api/v1/dashboard", headers=headers("viewer", "another-app")).status_code == 401
        assert client.get("/api/v1/dashboard", headers=headers("unknown")).status_code == 403
        assert client.get("/api/v1/dashboard", headers=headers("operator")).json()["role"] == "operator"
        result = client.post(f"/api/v1/events/{uuid4()}/actions", headers=headers("viewer"), json={
            "request_id": str(uuid4()), "state": "en_route", "expected_version": 1,
        })
        assert result.status_code == 403


def test_capture_jpeg_and_hls(tmp_path):
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    import shutil
    import subprocess
    from threading import Thread

    from monitoring.adapters import fetch_camera

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is required for the HLS fixture")
    Image.new("RGB", (80, 60), (90, 110, 150)).save(tmp_path / "still.jpg")
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=red:size=80x60:rate=5",
                    "-t", "1", "-c:v", "libx264", "-f", "hls", str(tmp_path / "stream.m3u8")], check=True)

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    handler = partial(QuietHandler, directory=tmp_path)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            jpeg = {"id": "a", "kind": "jpeg", "url": base + "/still.jpg"}
            status, data = fetch_camera(jpeg, ffmpeg, None)
            assert status == "saved" and data[1:3] == (80, 60)
            assert data[4] is not None
            assert fetch_camera(jpeg, ffmpeg, data[3]) == ("unchanged", None)
            status, data = fetch_camera({"id": "b", "kind": "hls", "url": base + "/stream.m3u8"}, ffmpeg, None)
            assert status == "saved" and data[1:3] == (80, 60)
        finally:
            server.shutdown()
            thread.join()


def test_legacy_model_leaves_severity_unknown(settings):
    from dataclasses import replace
    from monitoring.adapters import analyze

    config = replace(settings, mode="preview", model_url="http://model.example.test", model_contract="snow")

    def reply(request):
        assert request.url.path == "/v1/snow"
        return httpx.Response(200, json={"falling_snow": "yes", "format_valid": True, "evidence": "Visible flakes."})

    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        scene = analyze(client, config, b"frame")
    assert scene["falling_snow"] == "yes"
    assert scene["intensity"] == scene["visibility"] == scene["road_coverage"] == "uncertain"


def test_preview_run_and_demo_media_are_isolated(settings):
    from dataclasses import replace

    config = replace(settings, mode="preview")
    media = Media(settings)
    at = datetime.now(timezone.utc)
    camera = {"id": "scope-test", "name": "Контроль изоляции", "area": "Тест", "road": False}
    with connect(settings) as conn:
        prepare_run(conn, "preview", "preview", [camera])
        frame_id = store_frame(conn, "preview", camera["id"], (b"test", 10, 10, uuid4().hex, at, at),
                               media, analyze=False)
    with TestClient(create_app(config)) as client:
        real = client.get("/api/v1/dashboard").json()
        demo = client.get("/api/v1/dashboard?run=demo").json()
        assert real["mode"] == "preview" and demo["mode"] == "demo"
        assert any(item["frame_id"] == str(frame_id) for item in real["cameras"])
        assert all(item["frame_id"] != str(frame_id) for item in demo["cameras"])
        assert client.get(f"/api/v1/frames/{frame_id}").content == b"test"
        assert client.get(f"/api/v1/frames/{frame_id}?run=demo").status_code == 404


def test_v2_russian_conclusion_and_selective_snow_fallback():
    from monitoring.adapters import analyze

    config = Settings(model_url="http://model.example.test", model_contract="scene_v2")
    result = dict(schema_version="2", model="Weather VLM", prompt_version="snow-scene-5", format_valid=True,
                  falling_snow="yes", snowfall_intensity="heavy", blowing_snow="uncertain", snow_on_ground="yes",
                  large_snowdrifts="uncertain", snowdrift_on_road="uncertain", slush_on_road="uncertain",
                  road_coverage="uncertain", road_obstruction="uncertain", air_view="limited", ground_view="limited",
                  road_view="limited", roadside_view="limited", evidence="Dense flakes are visible.")
    calls = []

    def mock(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/snow":
            return httpx.Response(200, json={"falling_snow": "no", "format_valid": True})
        return httpx.Response(200, json=result)

    with httpx.Client(transport=httpx.MockTransport(mock)) as client:
        full = analyze(client, config, b"frame")
        assert calls == ["/v2/scene"]
        assert full["intensity"] == "heavy" and full["visibility"] == "uncertain"
        assert full["evidence"] == "Виден сильный снегопад. На земле виден снег. Обзор в кадре ограничен."
        now = datetime.now(timezone.utc)
        observations = [{"scene": full, "observed_at": now - timedelta(seconds=s)} for s in (0, 20)]
        assert event_signals(observations, True, 90) == {"snowfall": "urgent"}
        assert event_signals(observations, False, 90) == {}

        calls.clear()
        result.update(falling_snow="uncertain", snowfall_intensity="uncertain", road_coverage="clear")
        partial = analyze(client, config, b"frame")
        assert calls == ["/v2/scene", "/v1/snow"]
        assert partial["falling_snow"] == "no" and partial["intensity"] == "none"
        assert partial["road_coverage"] == "clear"
        assert partial["snow_source"] == "snow"
        assert partial["visibility"] == "uncertain"

        result["format_valid"] = False
        fallback = analyze(client, config, b"frame")
        assert fallback["details"] is None and fallback["road_coverage"] == "uncertain"
        assert fallback["evidence"] == "Падающего снега не видно."

        calls.clear()
        result.update(format_valid=True, air_view="unusable", ground_view="unusable", road_view="unusable", roadside_view="unusable")
        blank = analyze(client, config, b"frame")
        assert calls == ["/v2/scene"]
        assert blank["view_quality"] == "unusable" and blank["falling_snow"] == "uncertain"


def test_preview_keeps_previous_analysis_while_processing_new_frame(settings):
    from dataclasses import replace
    from monitoring.operations import record_observation

    config = replace(settings, mode="preview")
    camera_id = "test-" + uuid4().hex
    camera = {"id": camera_id, "name": "Кадры", "area": "Тест", "road": True}
    at = datetime.now(timezone.utc)
    with connect(settings) as conn:
        prepare_run(conn, "preview", "preview", [camera])
        media = Media(config)
        older = store_frame(conn, "preview", camera_id, (b"first", 1, 1, uuid4().hex, None, at - timedelta(seconds=10)), media)
        frame = conn.execute("SELECT * FROM frames WHERE id=%s", (older,)).fetchone()
        record_observation(conn, config, frame, model_fixture_rows()[0]["scene"])
        latest = store_frame(conn, "preview", camera_id, (b"second", 1, 1, uuid4().hex, None, at), media)
    with TestClient(create_app(config)) as client:
        camera_rows = client.get("/api/v1/dashboard").json()["cameras"]
        result = next(row for row in camera_rows if row["id"] == camera_id)
    assert result["frame_id"] == str(latest) and result["analyzed_frame_id"] == str(older)
    assert result["scene"]["falling_snow"] == "no"


def test_camera_location_and_slow_source_freshness(settings):
    from dataclasses import replace

    config = replace(settings, mode="preview")
    camera_id = "test-" + uuid4().hex
    coords = [158.639, 53.044]
    camera = {"id": camera_id, "name": "Панорама", "area": "Тест", "road": False,
              "coordinates": coords, "location_note": "Ориентир", "interval_seconds": 1800}
    now = datetime.now(timezone.utc)
    with connect(settings) as conn:
        prepare_run(conn, "preview", "preview", [camera])
        media = Media(config)
        frame = (b"frame", 10, 10, uuid4().hex, now - timedelta(minutes=45), now - timedelta(minutes=22))
        saved_id = store_frame(conn, "preview", camera_id, frame, media, analyze=False)
    with TestClient(create_app(config)) as client:
        result = next(row for row in client.get("/api/v1/dashboard").json()["cameras"] if row["id"] == camera_id)
    assert result["coordinates"] == coords and result["location_note"] == "Ориентир"
    assert result["frame_id"] == str(saved_id) and result["status"] == "available"


def test_five_day_forecast_fallback_and_interval():
    from monitoring.operations import warning_window

    calls = []
    at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=2)

    def endpoint(request):
        calls.append(request.url.path)
        if request.url.path.endswith("onecall"):
            return httpx.Response(401)
        return httpx.Response(200, json={"list": [{
            "dt": at.timestamp(), "main": {"temp": -2}, "weather": [{"id": 601}], "pop": .9,
            "snow": {"3h": 1.4}, "wind": {"speed": 5, "deg": 250, "gust": 9},
        }]})

    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        period, = forecast(client, "test", 53, 158)
    assert calls == ["/data/3.0/onecall", "/data/2.5/forecast"]
    assert period["horizon"] == "three_hourly" and period["snow_mm"] == 1.4 and period["gust_mps"] == 9
    window = warning_window([period], at - timedelta(hours=1), .5)
    assert datetime.fromisoformat(window["start"]) == at - timedelta(hours=3)
    assert datetime.fromisoformat(window["end"]) == at


def test_inspection_queue_explains_and_de_duplicates():
    from monitoring.dispatch import inspection_queue

    now = datetime.now(timezone.utc)
    base = dict(road=True, state="new", severity="watch", ended_at=None, evidence_stale=False,
                camera_id="A", id=uuid4(), name="Перекрёсток", area="Елизово", road_priority=1,
                road_priority_reason="Пешеходный переход", last_seen=now, kind="snowfall", reason="Снегопад")
    urgent = {**base, "id": uuid4(), "severity": "urgent"}
    result = inspection_queue([base, urgent, {**base, "camera_id": "B", "road_priority": 3},
                               {**urgent, "camera_id": "C", "evidence_stale": True},
                               {**urgent, "camera_id": "D", "state": "en_route"},
                               {**urgent, "camera_id": "E", "road": False}])
    assert [item["event_id"] for item in result] == [urgent["id"], base["id"]]
    assert result[0]["factors"][0] == "Пешеходный переход"


def test_comparison_feedback_and_after_work_are_scoped(settings):
    from monitoring.operations import record_observation

    run = "test-" + uuid4().hex
    other = "test-" + uuid4().hex
    media = Media(settings)
    now = datetime.now(timezone.utc)
    first, _, heavy, _ = [item["scene"] for item in model_fixture_rows()]
    cameras = [dict(id=key, name=key, area="Тест", road=True, road_priority=1,
                    road_priority_reason="Пешеходный переход") for key in ("a", "b")]
    with connect(settings) as conn:
        prepare_run(conn, run, "replay", cameras)
        prepare_run(conn, other, "replay", cameras)
        def save(camera, seconds, data, scene=None):
            frame_id = store_frame(conn, run, camera, (data, 40, 25, uuid4().hex, None, now + timedelta(seconds=seconds)), media)
            if scene:
                frame = conn.execute("SELECT * FROM frames WHERE id=%s", (frame_id,)).fetchone()
                record_observation(conn, settings, frame, scene)
            return str(frame_id)
        baseline = save("a", -37, b"clear", first)
        wrong_camera = save("b", -35, b"different-view", first)
        save("a", -28, b"snow-first", heavy)
        operating = save("a", -25, b"snow-second", heavy)
        after = save("a", -5, b"cleared")
        event = conn.execute("SELECT id FROM events WHERE run_id=%s", (run,)).fetchone()
    endpoint = f'/api/v1/events/{event["id"]}'
    with TestClient(create_app(settings)) as client:
        query = {"run": run}
        compare = client.get(endpoint + "/review", params=query).json()
        assert compare["before"]["id"] == baseline and compare["current"]["id"] == operating
        assert not compare["candidate"]
        dashboard = client.get("/api/v1/dashboard", params=query).json()
        assert len(dashboard["inspection_queue"]) == 1
        feedback = {"verdict": "confirmed"}
        first_result = client.put(endpoint + "/feedback", params=query, json=feedback)
        retry = client.put(endpoint + "/feedback", params=query, json=feedback)
        assert first_result.status_code == 200 and first_result.json() == retry.json()
        assert client.put(endpoint + "/feedback", params={"run": other}, json=feedback).status_code == 404
        assert client.get("/api/v1/dashboard", params=query).json()["feedback_counts"] == {"confirmed": 1}
        with connect(settings) as conn:
            conn.execute("UPDATE events SET state='en_route' WHERE id=%s", (event["id"],))
        assert client.get(endpoint + "/review", params=query).json()["candidate"]["id"] == after
        assert client.put(endpoint + "/after-work", params=query, json={"frame_id": wrong_camera}).status_code == 409
        assert client.put(endpoint + "/after-work", params=query, json={"frame_id": after}).json()["id"] == after
        assert client.get(endpoint + "/review", params=query).json()["after"]["id"] == after
