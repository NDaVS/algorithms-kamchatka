"""Load a labelled replay into an isolated run. It never sends external mail."""

import argparse
from dataclasses import replace
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .adapters import Media, encode_image
from .config import ROOT, Settings
from .db import check, connect, migrate, prepare_run
from .operations import record_observation, save_forecast, save_summary, store_frame
from .scene import Scene


def load_replay(settings, run_id, manifest, mode="replay"):
    media = Media(replace(settings, s3_bucket="") if mode == "demo" else settings)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    with connect(settings) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(442703)")
        if conn.execute("SELECT 1 FROM runs WHERE id=%s", (run_id,)).fetchone():
            return
        prepare_run(conn, run_id, mode, data["cameras"])
        for sample in sorted(data["frames"], key=lambda row: datetime.fromisoformat(row["at"])):
            path = (manifest.parent / sample["file"]).resolve()
            if not path.is_relative_to(manifest.parent.resolve()):
                raise ValueError("Replay files must be stored alongside the manifest")
            payload, width, height = encode_image(path.read_bytes())
            at = datetime.fromisoformat(sample["at"])
            if at.tzinfo is None:
                raise ValueError("Frame timestamp must include a timezone")
            digest = hashlib.sha256(payload).hexdigest()
            if sample.get("synthetic_repeat"):
                if mode != "demo":
                    raise ValueError("Synthetic repeats are available in demo only")
                digest = hashlib.sha256(f"{digest}:{at.isoformat()}".encode()).hexdigest()
            captured = (payload, width, height, digest, None, at)
            frame_id = store_frame(conn, run_id, sample["camera_id"], captured, media)
            if frame_id is None:
                continue
            features = Scene.model_validate(sample["scene"]).model_dump()
            frame = conn.execute("SELECT * FROM frames WHERE id=%s", (frame_id,)).fetchone()
            record_observation(conn, settings, frame, features)
            conn.execute("UPDATE jobs SET stage=%s WHERE frame_id=%s",
                         ("done" if features["format_valid"] else "failed", frame_id))
        for item in data.get("forecast", []):
            save_forecast(conn, settings, run_id, item["point"], item["periods"], datetime.fromisoformat(item["at"]))
        if data["frames"]:
            last_at = max(datetime.fromisoformat(sample["at"]) for sample in data["frames"])
            save_summary(conn, settings, run_id, last_at + timedelta(seconds=1), force=True)
        check(conn, run_id, "model", "fixtures")


def seed_demo(settings):
    directory = ROOT / "runtime" / "demo"
    manifest = directory / "manifest.json"
    if manifest.is_file():
        load_replay(settings, "demo", manifest, "demo")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--id", required=True, help="Run id")
    args = parser.parse_args()
    import re
    if not re.fullmatch(r"[a-z0-9-]{1,40}", args.id) or args.id in {"live", "demo", "preview"}:
        parser.error("Use a different id containing only lower-case letters, digits, and hyphens")
    settings = Settings()
    settings.validate()
    migrate(settings)
    load_replay(settings, args.id, args.manifest)


if __name__ == "__main__":
    main()
