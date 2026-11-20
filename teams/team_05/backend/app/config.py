"""App configuration (env overrides, plain pydantic, no pydantic-settings)."""
from __future__ import annotations

import os

from core.models import AppConfig

HOST = os.environ.get("SNEGOPLAN_HOST", "127.0.0.1")
PORT = int(os.environ.get("SNEGOPLAN_PORT", "8000"))
CORS_ORIGINS = os.environ.get("SNEGOPLAN_CORS", "http://localhost:5173").split(",")
USE_SYNTHETIC = os.environ.get("SNEGOPLAN_SYNTHETIC", "0") == "1"  # force synthetic graph


def default_config() -> AppConfig:
    return AppConfig(
        demo_auto_apply=os.environ.get("SNEGOPLAN_AUTO_APPLY", "1") == "1",
        auto_apply_delay_s=float(os.environ.get("SNEGOPLAN_AUTO_APPLY_DELAY", "8")),
        n_trucks=int(os.environ.get("SNEGOPLAN_TRUCKS", "2")),   # С-01, С-02 (set 0 to disable trucks)
    )

# Approximate positions (lon, lat) of depot "База №1" and dump "Снегоотвал №1" (placeholders inside MVP bbox;
# build_graph snaps them to the nearest graph nodes). Adjust to real sites.
DEPOT_LONLAT = (float(os.environ.get("SNEGOPLAN_DEPOT_LON", "158.645")), float(os.environ.get("SNEGOPLAN_DEPOT_LAT", "53.045")))
# DUMP_OUTSIDE: the dump lies outside the city bbox (north, on the exit road towards Елизово) and is connected to the
# graph at load time by a synthetic trunk link (see main.prepare_real_graph). False -> legacy "nearest inner node".
DUMP_OUTSIDE = os.environ.get("SNEGOPLAN_DUMP_OUTSIDE", "1") == "1"
_DUMP_DEF = ("158.66", "53.10") if DUMP_OUTSIDE else ("158.665", "53.060")
DUMP_LONLAT = (float(os.environ.get("SNEGOPLAN_DUMP_LON", _DUMP_DEF[0])), float(os.environ.get("SNEGOPLAN_DUMP_LAT", _DUMP_DEF[1])))
# Required (to-be-plowed) highway classes; only NAMED streets of these classes become tasks.
REQUIRED_HIGHWAYS = {"primary", "secondary", "tertiary", "residential", "unclassified", "trunk",
                     "primary_link", "secondary_link", "tertiary_link", "trunk_link"}
UNNAMED_LABELS = {"", "Безымянный проезд"}

# Shift end for every vehicle: "dump" (Снегоотвал) or "depot" (База). Single source: solver.cost.FINISH_AT
# (env SNEGOPLAN_FINISH_AT, default "dump").
from solver.cost import FINISH_AT  # noqa: E402,F401
# Dead-end stubs (a degree-1 endpoint) of these classes shorter than DEADEND_MAX_M are not plowed (yards, turnarounds).
DEADEND_HIGHWAYS = {"residential", "unclassified"}
DEADEND_MAX_M = 150.0
