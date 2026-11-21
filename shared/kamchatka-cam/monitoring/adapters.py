import hashlib
from datetime import datetime, timezone
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
import smtplib
import ssl
import tempfile
from typing import Literal

import httpx
from PIL import Image, ImageOps

from cameras.collect import capture
from .scene import Scene, SceneDetails, russian_conclusion, unknown


class Media:
    def __init__(self, settings):
        self.root = settings.media_dir
        self.bucket = settings.s3_bucket
        self.s3 = None
        if self.bucket:
            import boto3
            from botocore.config import Config
            self.s3 = boto3.client("s3", endpoint_url=settings.s3_endpoint or None,
                                   config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}))

    def put(self, run_id, data):
        digest = hashlib.sha256(data).hexdigest()
        key = f"{run_id}/{digest}.jpg"
        if self.s3:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType="image/jpeg")
        else:
            path = self.root / key
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as incoming:
                temporary = Path(incoming.name)
                try:
                    incoming.write(data)
                    incoming.close()
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
        return key

    def get(self, key):
        if self.s3:
            body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"]
            with body:
                return body.read()
        return (self.root / key).read_bytes()


def encode_image(data):
    with Image.open(BytesIO(data), formats=["JPEG", "PNG", "WEBP"]) as raw:
        if raw.width * raw.height > 16_800_000:
            raise ValueError("Image is too large")
        image = ImageOps.exif_transpose(raw).convert("RGB")
        image.thumbnail((1920, 1920))
        result = BytesIO()
        image.save(result, "JPEG", quality=87)
        encoded = result.getvalue()
    if len(encoded) > 2 * 1024 * 1024:
        raise ValueError("Frame exceeds model size limit")
    return encoded, image.width, image.height


def fetch_camera(camera, ffmpeg, previous_digest):
    from email.utils import parsedate_to_datetime
    from types import SimpleNamespace

    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary)
        args = SimpleNamespace(output=output, timeout=12, clip_seconds=0, ffmpeg=ffmpeg)
        result = capture(camera, args, previous_digest)
        if result["status"] != "saved":
            return result["status"], None
        data = Path(result["path"]).read_bytes()
    source_at = None
    if result.get("http_last_modified"):
        try:
            timestamp = parsedate_to_datetime(result["http_last_modified"])
            if timestamp.tzinfo is not None:
                source_at = timestamp.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            pass
    observed_at = datetime.fromisoformat(result["requested_at_utc"])
    encoded, width, height = encode_image(data)
    digest = result.get("sha256") or hashlib.sha256(encoded).hexdigest()
    return "saved", (encoded, width, height, digest, source_at, observed_at)


class SceneV2Response(SceneDetails):
    schema_version: Literal["2"]
    model: str
    prompt_version: str
    format_valid: bool
    falling_snow: Literal["yes", "no", "uncertain"]
    snowfall_intensity: Literal["none", "light", "moderate", "heavy", "uncertain"]
    snow_on_ground: Literal["yes", "no", "uncertain"]
    large_snowdrifts: Literal["yes", "no", "uncertain"]
    road_coverage: Literal["clear", "partial", "mostly_covered", "uncertain"]
    visibility: Literal["good", "reduced", "poor", "uncertain"] = "uncertain"


def analyze(client, settings, data):
    headers = {"Authorization": f"Bearer {settings.model_token}"} if settings.model_token else {}
    files = {"image": ("frame.jpg", data, "image/jpeg")}

    def fetch(path):
        response = client.post(f"{settings.model_url}{path}", headers=headers, files=files, timeout=30)
        response.raise_for_status()
        return response.json()

    contract = settings.model_contract
    if contract == "scene_v2":
        result = SceneV2Response.model_validate(fetch("/v2/scene"))
        usable = result.format_valid and any(
            view != "unusable" for view in (result.air_view, result.ground_view, result.road_view, result.roadside_view)
        )
        fields = {**unknown(), "schema_version": "2", "model": result.model, "prompt_version": result.prompt_version,
                  "format_valid": result.format_valid, "snow_source": "scene"}
        if usable:
            fields.update(
                falling_snow=result.falling_snow, intensity=result.snowfall_intensity, visibility=result.visibility,
                view_quality="good" if result.air_view == result.ground_view == "good" else "limited",
                snow_on_ground=result.snow_on_ground, large_snowdrifts=result.large_snowdrifts,
                road_coverage=result.road_coverage,
                details=SceneDetails.model_validate(result.model_dump()).model_dump(),
            )
        if not result.format_valid or usable and result.falling_snow == "uncertain":
            try:
                fallback = fetch("/v1/snow")
            except httpx.HTTPError:
                if not usable:
                    raise
            else:
                fallback_value = fallback.get("falling_snow")
                if fallback.get("format_valid") is True and fallback_value in ("yes", "no"):
                    fields.update(format_valid=True, falling_snow=fallback_value, snow_source="snow")
                    fields["intensity"] = "none" if fallback_value == "no" else "uncertain"
                    if not usable:
                        fields.update(view_quality="limited", details=None)
    elif contract == "snow":
        result = fetch("/v1/snow")
        valid = result.get("format_valid") is True
        snow = result.get("falling_snow") if valid else "uncertain"
        fields = {**unknown(), "schema_version": "1", "model": "Weather VLM", "prompt_version": "legacy-snow-only",
                  "format_valid": valid, "falling_snow": snow, "intensity": "none" if snow == "no" else "uncertain",
                  "view_quality": "limited" if valid else "unusable", "snow_source": "snow"}
    else:
        fields = fetch("/v1/scene")

    validated = Scene.model_validate(fields).model_dump()
    validated["evidence"] = russian_conclusion(validated)
    return validated


def forecast(client, key, latitude, longitude):
    params = {"lat": latitude, "lon": longitude, "appid": key, "units": "metric"}
    base = "https://api.openweathermap.org/data"
    response = client.get(f"{base}/3.0/onecall", params={**params, "exclude": "minutely,current,alerts"}, timeout=12)
    fallback = response.status_code in {401, 403}
    if fallback:
        response = client.get(f"{base}/2.5/forecast", params=params, timeout=12)
    response.raise_for_status()
    data = response.json()
    groups = (("three_hourly", data["list"]),) if fallback else (("hourly", data["hourly"]), ("daily", data["daily"]))
    periods = []
    for horizon, items in groups:
        for item in items:
            condition_snow = any(600 <= int(code["id"]) < 700 for code in item["weather"])
            snow = item.get("snow")
            if isinstance(snow, dict):
                snow = snow.get("3h" if fallback else "1h")
            temperature = item["main"].get("temp") if fallback else item["temp"]
            if horizon == "daily":
                temperature = temperature.get("day")
            wind = item.get("wind", {}) if fallback else {
                "speed": item.get("wind_speed"), "deg": item.get("wind_deg"), "gust": item.get("wind_gust"),
            }
            periods.append({
                "at": datetime.fromtimestamp(item["dt"], timezone.utc).isoformat(),
                "horizon": horizon, "probability": item.get("pop"), "temperature_c": temperature,
                "snow_mm": snow, "snow": condition_snow or (snow is not None and snow > 0),
                "wind_mps": wind.get("speed"), "wind_deg": wind.get("deg"), "gust_mps": wind.get("gust"),
            })
    if not periods:
        raise ValueError("Empty forecast")
    return periods


class AmbiguousDelivery(Exception):
    pass


def deliver(settings, row, attachment=None):
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = row["recipient"]
    message["Subject"] = row["subject"]
    host = httpx.URL(settings.base_url).host or "localhost"
    identity = hashlib.sha256(f'{row["run_id"]}:{row["key"]}:{row["recipient"]}'.encode()).hexdigest()
    message["Message-ID"] = f"<{identity}@{host}>"
    message.set_content(row["body"])
    if attachment:
        message.add_attachment(attachment, maintype="image", subtype="jpeg", filename="frame.jpg")
    context = ssl.create_default_context()
    if settings.mail_security == "ssl":
        server = smtplib.SMTP_SSL(settings.mail_host, settings.mail_port, timeout=10, context=context)
    else:
        server = smtplib.SMTP(settings.mail_host, settings.mail_port, timeout=10)
    try:
        if settings.mail_security == "starttls":
            server.starttls(context=context)
        if settings.mail_user:
            server.login(settings.mail_user, settings.mail_password)
        try:
            server.send_message(message)
        except (OSError, smtplib.SMTPServerDisconnected) as exc:
            raise AmbiguousDelivery from exc
    finally:
        server.close()
