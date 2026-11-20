"""File logging setup shared by the app and the data scripts. Logs go to <repo>/logs/ (UTF-8, rotating).
Only stdlib; safe to import anywhere."""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def _file_handler(filename: str, level: int = logging.DEBUG) -> logging.Handler:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(LOG_DIR / filename, maxBytes=2_000_000, backupCount=5,
                                             encoding="utf-8")
    h.setLevel(level)
    h.setFormatter(logging.Formatter(_FMT))
    h._snegoplan = filename  # type: ignore[attr-defined]
    return h


def _has(logger: logging.Logger, filename: str) -> bool:
    return any(getattr(h, "_snegoplan", None) == filename for h in logger.handlers)


def setup_logging(filename: str = "backend.log", level: int = logging.INFO, console: bool = True) -> Path:
    """Idempotent. Root logger -> logs/<filename> (+ console). Also hooks uvicorn loggers and
    uncaught exceptions (sys.excepthook). Never raises (a broken log dir must not stop the app)."""
    try:
        root = logging.getLogger()
        root.setLevel(level)
        if not _has(root, filename):
            root.addHandler(_file_handler(filename))
            if console and not any(getattr(h, "_snegoplan_console", False) for h in root.handlers):
                c = logging.StreamHandler(sys.stderr)
                c.setLevel(level)
                c.setFormatter(logging.Formatter(_FMT))
                c._snegoplan_console = True  # type: ignore[attr-defined]
                root.addHandler(c)
            for n in ("uvicorn", "uvicorn.error", "uvicorn.access"):
                lg = logging.getLogger(n)
                if not _has(lg, filename) and not lg.propagate:
                    lg.addHandler(_file_handler(filename))
            prev = sys.excepthook

            def hook(tp, val, tb):
                logging.getLogger("snegoplan").critical("Uncaught exception", exc_info=(tp, val, tb))
                prev(tp, val, tb)
            sys.excepthook = hook
    except Exception as e:  # noqa: BLE001
        print(f"[logsetup] cannot set up file logging: {e}", file=sys.stderr)
    return LOG_DIR / filename


def frontend_logger() -> logging.Logger:
    """Separate logger that writes ONLY to logs/frontend.log (no propagation)."""
    lg = logging.getLogger("snegoplan.frontend")
    if not _has(lg, "frontend.log"):
        try:
            lg.addHandler(_file_handler("frontend.log", logging.INFO))
        except Exception as e:  # noqa: BLE001
            print(f"[logsetup] frontend log: {e}", file=sys.stderr)
        lg.setLevel(logging.INFO)
        lg.propagate = False
    return lg
