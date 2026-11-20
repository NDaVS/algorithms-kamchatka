"""Refresh the standalone dashboard's data from the Python planner."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from planner import plan_shift

fixture = json.loads((ROOT / "scenarios/demo-shift.json").read_text())
data = {
    "fixture": fixture,
    "runs": {variant["id"]: plan_shift(fixture, variant["id"]) for variant in fixture["variants"]},
}
(ROOT / "demo/data.js").write_text(
    "window.shiftData = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
    encoding="utf-8",
)
