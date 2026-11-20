"""Shared helpers for data scripts. Makes `backend/` importable and the console utf-8 safe (Windows)."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from core.logsetup import setup_logging  # noqa: E402

LOG_FILE = setup_logging("prepare_data.log", console=False)
log = logging.getLogger("snegoplan.prepare")

from core.graph import BASEMAP_PATH, BBOX, DATA_DIR, GRAPH_PATH  # noqa: E402

RAW_GRAPH_PATH = DATA_DIR / "osm_drive.graphml"
LAYERS = ("roads", "water", "parks", "buildings")


def say(msg: str) -> None:
    print(msg, flush=True)
    log.info(msg.strip())


def log_exc(where: str) -> None:
    """Log the active exception with full traceback to logs/prepare_data.log and tell the user."""
    log.exception("FAILED: %s", where)
    print(f"  Подробности (traceback) записаны в {LOG_FILE}", flush=True)


def empty_collection() -> dict:
    return {"type": "FeatureCollection", "features": []}


def write_basemap(features: list[dict], path: Path = BASEMAP_PATH) -> None:
    """Single FeatureCollection; each feature has properties.layer in LAYERS (CONTRACTS.md section 5)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "bbox": list(BBOX), "features": features},
                               ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
