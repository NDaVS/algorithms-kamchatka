#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
mode=demo
case "${1:-}" in
  "") ;;
  --preview) mode=preview ;;
  -h|--help)
    printf 'Usage: %s [--preview]\n' "$0"
    exit 0
    ;;
  *) printf 'Usage: %s [--preview]\n' "$0" >&2; exit 2 ;;
esac
if (( $# > 1 )); then
  printf 'Usage: %s [--preview]\n' "$0" >&2
  exit 2
fi

docker compose version >/dev/null
docker info >/dev/null
python3 - "$mode" <<'PY'
import os
from pathlib import Path
import secrets
import sys

path = Path(".env")
if path.exists():
    print("Using existing .env")
else:
    config = Path(".env.example").read_text(encoding="utf-8")
    config = config.replace("MONITOR_MODE=demo\n", f"MONITOR_MODE={sys.argv[1]}\n", 1)
    config = config.replace("POSTGRES_PASSWORD=\n", f"POSTGRES_PASSWORD={secrets.token_hex(24)}\n", 1)
    with os.fdopen(os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600), "w", encoding="utf-8") as target:
        target.write(config)
PY

if [[ ! -f runtime/demo/manifest.json ]]; then
  python3 fixtures/prepare_demo.py fixtures/sample/1.png fixtures/sample/2.png fixtures/sample/3.png fixtures/sample/4.png
fi

docker compose build api
docker compose up -d --no-build
binding=$(docker compose port api 8080)
python3 - "http://$binding" <<'PY'
import json
import sys
from time import monotonic, sleep
from urllib.request import urlopen

url = sys.argv[1]
deadline = monotonic() + 45
while monotonic() < deadline:
    try:
        with urlopen(url + "/healthz", timeout=3) as response:
            if json.load(response).get("status") == "ready":
                print(f"Dashboard: {url}")
                break
    except (OSError, ValueError):
        sleep(1)
else:
    sys.exit("The API did not become ready. Check docker compose logs api worker.")
PY
