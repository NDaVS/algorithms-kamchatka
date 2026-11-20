"""Run the manual dispatcher on a shared SQLite store in a trusted environment."""

import argparse
import json
import re
import sqlite3
from contextlib import closing
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

DEMO = Path(__file__).resolve().parent / "demo"
SHIFT_PATH = re.compile(r"/api/shifts/([a-f0-9]{32})\Z")
ASSETS = {
    "/", "/index.html", "/screen.css", "/road-links.js", "/petropavlovsk.js",
    "/shift.js", "/city.js", "/workspace-store.js", "/core-pilot.js", "/demo-plans.js",
}
MAX_BODY = 8 * 1024 * 1024


class Conflict(Exception):
    pass


class ShiftRepository:
    def __init__(self, db):
        self.db = db
        with closing(self.connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS shifts (
                    id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    title TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, closed_at TEXT,
                    snapshot TEXT NOT NULL
                )"""
            )

    def connect(self):
        return sqlite3.connect(self.db, timeout=5)

    def list(self):
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT id, title, status, created_at, closed_at, revision "
                "FROM shifts ORDER BY created_at DESC"
            ).fetchall()
        fields = ("id", "title", "status", "createdAt", "closedAt", "revision")
        return [dict(zip(fields, row)) for row in rows]

    def read(self, shift_id):
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT revision, snapshot FROM shifts WHERE id = ?", (shift_id,)
            ).fetchone()
        return None if row is None else {"revision": row[0], "snapshot": json.loads(row[1])}

    def write(self, snapshot, expected):
        data = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT revision, status, snapshot FROM shifts WHERE id = ?", (snapshot["id"],)
            ).fetchone()
            revision = 0 if current is None else current[0]
            if revision != expected or (current is not None and current[1] == "closed"):
                raise Conflict()
            if current is not None:
                prior = json.loads(current[2])
                if prior["createdAt"] != snapshot["createdAt"] or (
                    prior["events"] and snapshot["events"][-len(prior["events"]):] != prior["events"]
                ):
                    raise Conflict()
            connection.execute(
                """INSERT INTO shifts (id, revision, title, status, created_at, closed_at, snapshot)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET revision = excluded.revision, title = excluded.title,
                     status = excluded.status, closed_at = excluded.closed_at, snapshot = excluded.snapshot""",
                (
                    snapshot["id"], revision + 1, snapshot["title"], snapshot["status"],
                    snapshot["createdAt"], snapshot.get("closedAt"), data,
                ),
            )
        return {"revision": revision + 1, "snapshot": snapshot}


def valid_snapshot(snapshot, shift_id):
    return (
        isinstance(snapshot, dict)
        and snapshot.get("version") == 2
        and snapshot.get("id") == shift_id
        and isinstance(snapshot.get("title"), str)
        and 0 < len(snapshot["title"].strip()) <= 100
        and snapshot.get("status") in {"open", "closed"}
        and isinstance(snapshot.get("createdAt"), str)
        and isinstance(snapshot.get("jobs"), list)
        and isinstance(snapshot.get("events"), list)
        and isinstance(snapshot.get("vehicleIds"), list)
        and (
            snapshot["status"] != "closed"
            or (
                isinstance(snapshot.get("closedAt"), str)
                and all(isinstance(job, dict) and job.get("state") in {"accepted", "canceled", "transferred"}
                        for job in snapshot["jobs"])
            )
        )
    )


def handler_for(repository):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(DEMO), **kwargs)

        def body(self, status, content, media_type):
            self.send_response(status)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(content)

        def reply(self, status, value):
            self.body(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def get_resource(self):
            url = urlsplit(self.path)
            path = url.path
            if path == "/api/shifts":
                self.reply(200, repository.list())
            elif path == "/api/plans" and self.command == "GET":
                try:
                    from planning_bridge import calculate
                    query = parse_qs(url.query, max_num_fields=8)
                    self.reply(200, calculate(query.get("case", ["base_snowfall"])[0],
                                              int(query.get("seed", ["42"])[0])))
                except ValueError:
                    self.reply(400, {"error": "Недопустимый сценарий или номер варианта."})
                except BlockingIOError as error:
                    self.reply(409, {"error": str(error)})
                except ImportError:
                    self.reply(503, {"error": "Для расчёта серверу нужны зависимости requirements-core.txt."})
                except RuntimeError:
                    self.reply(500, {"error": "Ядро не вернуло проверенный план."})
            elif match := SHIFT_PATH.fullmatch(path):
                row = repository.read(match[1])
                self.reply(200, row) if row else self.reply(404, {"error": "shift_not_found"})
            elif path == "/workspace-config.js":
                self.body(200, b"window.sharedWorkspace = true;\n", "text/javascript")
            elif path in ASSETS:
                if self.command == "HEAD":
                    super().do_HEAD()
                else:
                    super().do_GET()
            else:
                self.reply(404, {"error": "not_found"})

        do_GET = get_resource
        do_HEAD = get_resource

        def do_PUT(self):
            match = SHIFT_PATH.fullmatch(urlsplit(self.path).path)
            if not match:
                self.reply(404, {"error": "not_found"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                expected = int(self.headers.get("If-Match", "-1"))
                if not 0 < size <= MAX_BODY:
                    self.reply(413, {"error": "invalid_size"})
                    return
                if expected < 0 or self.headers.get_content_type() != "application/json":
                    self.reply(400, {"error": "expected_revision_and_json"})
                    return
                snapshot = json.loads(self.rfile.read(size))
                if not valid_snapshot(snapshot, match[1]):
                    self.reply(400, {"error": "invalid_shift"})
                    return
                self.reply(200, repository.write(snapshot, expected))
            except Conflict:
                self.reply(409, {"error": "shift_conflict"})
            except (ValueError, TypeError, UnicodeError):
                self.reply(400, {"error": "invalid_json"})
            except sqlite3.Error:
                self.reply(503, {"error": "database_unavailable"})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=Path("runtime/shifts.sqlite3"))
    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    repository = ShiftRepository(args.db)
    with ThreadingHTTPServer((args.bind, args.port), handler_for(repository)) as server:
        print(f"http://{args.bind}:{server.server_port}/ · {args.db}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
