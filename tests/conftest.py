# tests/conftest.py
"""
Shared pytest fixtures.

Key thing this solves: database.py hardcodes its DB_PATH to
app/database/app.db. Tests must never touch that real file (it's the
person's actual download history). Every test gets its own throwaway
SQLite file instead, via the `db` fixture — swaps database.DB_PATH,
resets the thread-local connection cache, runs init_db() fresh, and
cleans up afterward regardless of pass/fail.
"""

import os
import sys
import tempfile

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
for sub in ("", "database", "downloader", "cookies", "scanner", "converter", "api"):
    p = os.path.join(_APP_DIR, sub) if sub else _APP_DIR
    if p not in sys.path:
        sys.path.insert(0, p)

import database as db  # noqa: E402


@pytest.fixture
def temp_db(monkeypatch):
    """Points database.py at a fresh temp SQLite file for the duration of
    one test, and makes sure the next test doesn't inherit a stale
    thread-local connection pointing at the old (now-deleted) file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # init_db() should create it fresh

    monkeypatch.setattr(db, "DB_PATH", path)
    db._local = __import__("threading").local()  # drop any cached connection from a prior test
    db.init_db()

    yield db

    # Best-effort cleanup — WAL mode may leave -wal/-shm siblings too.
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass
