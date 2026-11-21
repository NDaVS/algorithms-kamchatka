import json
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
LOCAL_TZ = ZoneInfo("Asia/Kamchatka")


@dataclass(frozen=True)
class Settings:
    database: str = os.getenv("DATABASE_URL", "")
    mode: str = os.getenv("MONITOR_MODE", "demo")
    media_dir: Path = Path(os.getenv("MEDIA_DIR", str(ROOT / "runtime" / "media")))
    s3_bucket: str = os.getenv("S3_BUCKET", "")
    s3_endpoint: str = os.getenv("S3_ENDPOINT", "")
    model_url: str = os.getenv("MODEL_URL", "").rstrip("/")
    model_contract: str = os.getenv("MODEL_CONTRACT", "scene")
    model_token: str = os.getenv("MODEL_TOKEN", "")
    weather_key: str = os.getenv("OPENWEATHER_KEY", "")
    cameras_path: Path = Path(os.getenv("MONITOR_CAMERAS", str(ROOT / "monitoring" / "cameras.json")))
    base_url: str = os.getenv("PUBLIC_URL", "http://localhost:8080").rstrip("/")
    oidc_issuer: str = os.getenv("OIDC_ISSUER", "").rstrip("/")
    oidc_jwks: str = os.getenv("OIDC_JWKS_URL", "")
    oidc_audience: str = os.getenv("OIDC_AUDIENCE", "")
    operators: tuple = tuple(filter(None, os.getenv("OPERATOR_SUBJECTS", "").split(",")))
    viewers: tuple = tuple(filter(None, os.getenv("VIEWER_SUBJECTS", "").split(",")))
    recipients: tuple = tuple(sorted(set(value.strip() for value in os.getenv("MAIL_TO", "").split(",") if value.strip())))
    mail_test: bool = os.getenv("MONITOR_MAIL_TEST") == "1"
    mail_host: str = os.getenv("SMTP_HOST", "")
    mail_port: int = int(os.getenv("SMTP_PORT", "587"))
    mail_security: str = os.getenv("SMTP_SECURITY", "starttls")
    mail_user: str = os.getenv("SMTP_USER", "")
    mail_password: str = os.getenv("SMTP_PASSWORD", "")
    mail_from: str = os.getenv("MAIL_FROM", "monitor@localhost")
    snow_probability: float = float(os.getenv("SNOW_PROBABILITY", "0.5"))
    summary_hour: int = int(os.getenv("SUMMARY_HOUR", "7"))
    freshness: int = int(os.getenv("FRAME_FRESHNESS_SECONDS", "90"))

    def validate(self):
        if not self.database:
            raise ValueError("DATABASE_URL is required")
        if self.mode not in {"demo", "replay", "preview", "live"}:
            raise ValueError("MONITOR_MODE must be demo, replay, preview, or live")
        if self.model_contract not in {"snow", "scene", "scene_v2"} or self.mode == "live" and self.model_contract == "snow":
            raise ValueError("Invalid MODEL_CONTRACT for this mode")
        if self.mail_security not in {"none", "starttls", "ssl"}:
            raise ValueError("Invalid SMTP_SECURITY")
        if self.mail_test and (
            self.mode != "preview" or (self.mail_host, self.mail_port, self.mail_security) != ("mailpit", 1025, "none")
        ):
            raise ValueError("MONITOR_MAIL_TEST requires preview and the local Mailpit service")
        if not 0 <= self.summary_hour < 24 or not 0 <= self.snow_probability <= 1 or self.freshness < 10:
            raise ValueError("Invalid monitoring threshold or schedule")
        if self.mode == "live" and not all([
            self.oidc_issuer.startswith("https://"), self.oidc_jwks.startswith("https://"),
            self.oidc_audience, self.operators or self.viewers,
            self.model_url.startswith("https://"), self.base_url.startswith("https://"),
        ]):
            raise ValueError("Live requires OIDC, HTTPS MODEL_URL, and HTTPS PUBLIC_URL")
        if self.mode == "live" and self.recipients and (not self.mail_host or self.mail_security == "none"):
            raise ValueError("Live mail requires a configured TLS SMTP transport")

    def cameras(self):
        sources = {source["id"]: source for source in json.loads((ROOT / "cameras" / "sources.json").read_text())}
        items = json.loads(self.cameras_path.read_text())
        if len({item["id"] for item in items}) != len(items):
            raise ValueError("Camera ids must be unique")
        for item in items:
            camera = {**sources.get(item["id"], {}), **item}
            if (camera.get("kind") not in {"hls", "jpeg"} or not camera.get("url")
                    or not isinstance(camera.get("interval_seconds"), int) or camera["interval_seconds"] < 10
                    or not isinstance(camera.get("road"), bool) or not camera.get("name") or not camera.get("area")):
                raise ValueError(f"Invalid camera configuration: {camera['id']}")
            weight = camera.get("road_priority", 0)
            if type(weight) is not int or not 0 <= weight <= 3 or weight and (
                not camera["road"] or not isinstance(camera.get("road_priority_reason"), str)
                or not camera["road_priority_reason"].strip()
            ):
                raise ValueError(f"Invalid road priority: {camera['id']}")
            coordinates = camera.get("coordinates")
            if coordinates is not None and (
                not isinstance(coordinates, list) or len(coordinates) != 2
                or any(type(value) not in (int, float) for value in coordinates)
                or not -180 <= coordinates[0] <= 180 or not -90 <= coordinates[1] <= 90
            ):
                raise ValueError(f"Invalid camera coordinates: {camera['id']}")
            yield camera
