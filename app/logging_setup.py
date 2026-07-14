# app/logging_setup.py
"""
Central logging — one rotating log file instead of print()/silently
swallowed exceptions. Anything that fails in a background thread
(download worker, converter worker, a scan) previously only surfaced as
a DB error_message with no stack trace and no way to correlate timing
across jobs. This gives every module the same logger, writing to
logs/app.log next to the downloads folder, rotated at 5MB x 5 files so
it never grows unbounded.

Usage in any module:
    from logging_setup import get_logger
    log = get_logger(__name__)
    log.info("...")
    log.exception("...")   # inside an except block — includes traceback
"""

import os
import logging
from logging.handlers import RotatingFileHandler

_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "app.log")
_configured = False


def _configure_once():
    global _configured
    if _configured:
        return
    os.makedirs(_LOG_DIR, exist_ok=True)

    root = logging.getLogger("ytdl_app")
    root.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(_LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(levelname)-8s %(name)s: %(message)s"))
    root.addHandler(console_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    _configure_once()
    return logging.getLogger(f"ytdl_app.{name}")
