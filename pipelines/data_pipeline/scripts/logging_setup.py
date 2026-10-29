"""One logging setup for every stage: the terminal and a rotating file in logs/.

Set RIFFLE_LOG_DIR to write somewhere else. Pointing it at the repository's top-level
logs/ folder lets the local Loki stack (infra/) pick the files up.
"""

from __future__ import annotations

import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_MARK = "_riffle_stage_handler"


def log_dir() -> Path:
    return Path(os.environ.get("RIFFLE_LOG_DIR") or ROOT / "logs")


def setup_logging(stage: str, level: int = logging.INFO) -> Path:
    """Log to stderr and to logs/<stage>.log. Safe to call twice: it replaces its own handlers."""
    directory = log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stage}.log"

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    formatter.converter = time.gmtime  # timestamps are UTC, matching the trailing Z
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8"),
    ]

    root = logging.getLogger()
    for old in [h for h in root.handlers if getattr(h, _MARK, False)]:
        root.removeHandler(old)
        old.close()
    for handler in handlers:
        handler.setFormatter(formatter)
        setattr(handler, _MARK, True)
        root.addHandler(handler)
    root.setLevel(level)
    return path
