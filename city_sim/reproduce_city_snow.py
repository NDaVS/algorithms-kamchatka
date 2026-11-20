"""Run and collect citywide scenarios from a running City Sim API."""
import argparse
from datetime import datetime
import gzip
from hashlib import sha256
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen


def run(base, payload, output):
    def read(path, data=None):
        body = json.dumps(data).encode() if data is not None else None
        headers = {"Content-Type": "application/json"} if body else {}
        with urlopen(Request(base + path, body, headers), timeout=60) as response:
            return response.read()

    endpoint = "/api/v1/operations/city/runs"
    submission = json.loads(read(endpoint, payload))
    path = f"{endpoint}/{submission['run_id']}"
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        result = json.loads(read(path))
        if result["status"] == "failed":
            raise RuntimeError(result["error"])
        if result["status"] == "completed":
            artifact = read(f"{path}/replay")
            filename = f"{result['run_id']}.json.gz"
            (output / filename).write_bytes(artifact)
            return {"input": result["input"], "metrics": result["metrics"], "provenance": result["provenance"],
                    "run_id": result["run_id"], "artifact": filename, "sha256": sha256(artifact).hexdigest(),
                    "content_sha256": sha256(gzip.decompress(artifact)).hexdigest()}
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8008")
    parser.add_argument("--output", type=Path, default=Path("out/citywide-snow"))
    parser.add_argument("--openweather", action="append", type=datetime.fromisoformat, default=[])
    parser.add_argument("--event", action="append", default=[])
    args = parser.parse_args()
    if not args.event and not args.openweather:
        parser.error("Choose one or more --event or --openweather inputs.")
    args.output.mkdir(parents=True, exist_ok=True)
    inputs = [{"source": "openweather", "valid_at": t.isoformat()} for t in args.openweather]
    inputs += [{"source": "reanalysis", "event_id": event} for event in args.event]
    runs = []
    for payload in inputs:
        replay = run(args.url.rstrip("/"), payload, args.output)
        runs.append(replay)
        print(json.dumps({k: replay[k] for k in ("input", "run_id", "metrics", "content_sha256")},
                         ensure_ascii=False), flush=True)
    (args.output / "manifest.json").write_text(json.dumps(runs, ensure_ascii=False, indent=2)+"\n")


if __name__ == "__main__":
    main()
