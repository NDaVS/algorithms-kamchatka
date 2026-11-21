"""Collect public camera snapshots and media feeds made available by their operators."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT.parent / "runtime" / "cameras"
MAX_JPEG_BYTES = 20 * 1024 * 1024


def capture(source, args, previous_digest):
    camera_id = source["id"]
    kind = source["kind"]
    url = source["url"]
    if kind == "embed":
        url = os.environ.get("KAMCHATKA_STELA_STREAM")
        if not url:
            return {"status": "needs_feed", "detail": "Публичный плеер. Нужен адрес HLS или RTSP от оператора."}
        if urlsplit(url).scheme not in {"http", "https", "rtsp", "rtsps"}:
            return {"status": "error", "detail": "KAMCHATKA_STELA_STREAM должен содержать URL HLS или RTSP."}
        kind = "video"

    started_at = datetime.now(timezone.utc)
    result = {"requested_at_utc": started_at.isoformat(timespec="milliseconds")}
    if kind == "jpeg" and args.clip_seconds:
        return {**result, "status": "snapshot_only"}
    if kind != "jpeg" and not args.ffmpeg:
        return {**result, "status": "error", "detail": "FFmpeg отсутствует в PATH."}

    try:
        folder = args.output / camera_id
        folder.mkdir(parents=True, exist_ok=True)
        suffix = ".mkv" if args.clip_seconds else ".jpg"
        filename = started_at.strftime("%Y%m%dT%H%M%S.%fZ") + suffix

        with tempfile.TemporaryDirectory(prefix=".capture-", dir=folder) as temporary:
            target = Path(temporary) / filename
            if kind == "jpeg":
                request = Request(url, headers={"User-Agent": "KamchatkaCameraCollector/1.0"})
                with urlopen(request, timeout=args.timeout) as response:
                    data = response.read(MAX_JPEG_BYTES + 1)
                    result["http_last_modified"] = response.headers.get("Last-Modified")
                if len(data) > MAX_JPEG_BYTES or not data.startswith(b"\xff\xd8\xff") or b"\xff\xd9" not in data[-32:]:
                    raise ValueError("Ответ не содержит полный JPEG допустимого размера.")
                digest = hashlib.sha256(data).hexdigest()
                result["sha256"] = digest
                if digest == previous_digest:
                    return {**result, "status": "unchanged"}
                target.write_bytes(data)
            else:
                command = [
                    args.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-rw_timeout", str(int(args.timeout * 1_000_000)),
                ]
                if urlsplit(url).scheme in {"rtsp", "rtsps"}:
                    command += ["-rtsp_transport", "tcp"]
                command += ["-i", url, "-map", "0:v:0", "-an", "-sn", "-dn"]
                if args.clip_seconds:
                    command += ["-t", str(args.clip_seconds), "-c:v", "copy"]
                else:
                    command += ["-frames:v", "1", "-q:v", "2", "-update", "1"]
                process = subprocess.run(
                    [*command, str(target)], capture_output=True, text=True, errors="replace",
                    timeout=args.timeout + args.clip_seconds,
                )
                if process.returncode or not target.is_file() or not target.stat().st_size:
                    detail = re.sub(r"(?:https?|rtsps?|tcp|tls|udp)://[^\s'\]]+", "[URL]", process.stderr)
                    raise ValueError(" ".join(detail.split())[:400] or "FFmpeg не получил видео.")

            size = target.stat().st_size
            destination = folder / filename
            target.replace(destination)
            return {**result, "status": "saved", "path": str(destination), "bytes": size}
    except HTTPError as error:
        detail = f"HTTP {error.code}"
    except URLError as error:
        detail = str(error.reason)
    except subprocess.TimeoutExpired:
        detail = "Истекло время ожидания FFmpeg."
    except (OSError, ValueError) as error:
        detail = str(error)
    return {**result, "status": "error", "detail": detail}


def main():
    sources = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="Показать каталог источников в JSON.")
    parser.add_argument("--source", action="append", choices=[source["id"] for source in sources])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--interval", type=float, default=0, help="Повторять сбор с указанным минимальным интервалом в секундах.")
    parser.add_argument("--clip-seconds", type=int, default=0, help="Записать видео указанной длительности вместо JPEG.")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    if (
        not math.isfinite(args.interval) or args.interval < 0 or args.clip_seconds < 0
        or not math.isfinite(args.timeout) or args.timeout <= 0 or args.workers < 1
    ):
        parser.error("Интервал и длительность должны быть неотрицательными, тайм-аут и число потоков — положительными.")

    selected = [source for source in sources if not args.source or source["id"] in args.source]
    if args.list:
        print(json.dumps(selected, ensure_ascii=False, indent=2))
        return 0

    args.output = args.output.expanduser().resolve()
    args.ffmpeg = shutil.which("ffmpeg")
    deadlines = dict.fromkeys((source["id"] for source in selected), 0.0)
    digests = {}
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            while True:
                now = time.monotonic()
                pending = [source for source in selected if deadlines[source["id"]] <= now]
                jobs = [(source, executor.submit(capture, source, args, digests.get(source["id"]))) for source in pending]
                failed = False
                for source, future in jobs:
                    camera_id = source["id"]
                    result = future.result()
                    print(json.dumps({"camera": camera_id, **result}, ensure_ascii=False), flush=True)
                    if result["status"] == "saved" and "sha256" in result:
                        digests[camera_id] = result["sha256"]
                    failed = failed or result["status"] in {"error", "needs_feed"}
                    deadlines[camera_id] = time.monotonic() + max(args.interval, source["interval_seconds"])
                if not args.interval:
                    return int(failed)
                time.sleep(max(0, min(deadlines.values()) - time.monotonic()))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
