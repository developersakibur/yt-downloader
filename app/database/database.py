# app/database/database.py
"""
Database — Single Source of Truth.

Rule 1: Scanner never downloads, Downloader never scans, UI never touches
        yt-dlp directly. Everything communicates through this module.
Rule 2: Every piece of application state lives here. Nothing important
        is ever kept only in a Python variable.

This module is intentionally the ONLY place in the project that runs
raw SQL. Every other module (scanner, worker, api, ui) must go through
the functions defined here.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, "app", "database", "app.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "app", "database", "schema.sql")

# A single process-wide lock around writes. SQLite handles concurrent
# readers fine on its own (WAL mode), but we serialize writes to avoid
# "database is locked" errors when multiple workers update jobs at once.
_write_lock = threading.Lock()
_local = threading.local()


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_conn():
    """One connection per thread, reused for the lifetime of that thread."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _connect()
    return _local.conn


@contextmanager
def cursor(write=False):
    """Yield a cursor. Commits on success, rolls back on error.
    Writes are serialized through _write_lock to keep state consistent."""
    conn = get_conn()
    if write:
        with _write_lock:
            cur = conn.cursor()
            try:
                yield cur
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    else:
        cur = conn.cursor()
        try:
            yield cur
        finally:
            pass


def init_db():
    """Create the database file and schema if they don't exist yet.
    Safe to call every time the app starts."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = _connect()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    _migrate(conn)
    conn.close()


def _migrate(conn):
    """One-off column additions for databases created before a schema
    change. CREATE TABLE IF NOT EXISTS (above) only creates missing
    tables — it never alters an existing table's columns, so anything
    added to video_jobs after the initial release needs an explicit
    ALTER TABLE here. Each one is wrapped individually so an already-
    applied migration (column already exists) is just skipped."""
    migrations = [
        "ALTER TABLE video_jobs ADD COLUMN quality TEXT NOT NULL DEFAULT 'best'",
    ]
    for stmt in migrations:
        try:
            conn.execute(stmt)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists — already migrated


def _row_to_dict(row):
    return dict(row) if row is not None else None


def _rows_to_dicts(rows):
    return [dict(r) for r in rows]


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# =================================================================
# GROUPS
# =================================================================

def create_group(type_, name, source_url, folder_path=None, total_videos=0):
    with cursor(write=True) as cur:
        cur.execute(
            """INSERT INTO groups (type, name, source_url, folder_path, total_videos)
               VALUES (?, ?, ?, ?, ?)""",
            (type_, name, source_url, folder_path, total_videos),
        )
        return cur.lastrowid


def get_group(group_id):
    with cursor() as cur:
        cur.execute("SELECT * FROM groups WHERE id = ?", (group_id,))
        return _row_to_dict(cur.fetchone())


def update_group(group_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [group_id]
    with cursor(write=True) as cur:
        cur.execute(f"UPDATE groups SET {cols} WHERE id = ?", values)


def list_groups():
    with cursor() as cur:
        cur.execute("SELECT * FROM groups ORDER BY created_at DESC")
        return _rows_to_dicts(cur.fetchall())


# =================================================================
# VIDEO JOBS
# =================================================================

JOB_FIELDS = (
    "group_id", "video_id", "url", "original_title", "thumbnail_path",
    "duration", "uploader", "playlist_index", "format", "quality",
)


def create_job(url, format="MP4", quality="best", group_id=None, video_id=None,
                original_title=None, thumbnail_path=None, duration=None,
                uploader=None, playlist_index=None):
    """Scanner calls this for every video it discovers.
    Job always starts as 'queued'."""
    with cursor(write=True) as cur:
        cur.execute(
            """INSERT INTO video_jobs
               (group_id, video_id, url, original_title, thumbnail_path,
                duration, uploader, playlist_index, format, quality, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued')""",
            (group_id, video_id, url, original_title, thumbnail_path,
             duration, uploader, playlist_index, format, quality),
        )
        return cur.lastrowid


def get_job(job_id):
    with cursor() as cur:
        cur.execute("SELECT * FROM video_jobs WHERE id = ?", (job_id,))
        return _row_to_dict(cur.fetchone())


def list_jobs(status=None, group_id=None):
    query = "SELECT * FROM video_jobs WHERE 1=1"
    params = []
    if status:
        if isinstance(status, (list, tuple)):
            placeholders = ", ".join("?" for _ in status)
            query += f" AND status IN ({placeholders})"
            params.extend(status)
        else:
            query += " AND status = ?"
            params.append(status)
    if group_id is not None:
        query += " AND group_id = ?"
        params.append(group_id)
    query += " ORDER BY created_at DESC"
    with cursor() as cur:
        cur.execute(query, params)
        return _rows_to_dicts(cur.fetchall())


def update_job(job_id, **fields):
    """Generic state update used by Scanner, Worker, and UI actions
    (pause/resume/retry/skip/delete all funnel through here)."""
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    with cursor(write=True) as cur:
        cur.execute(f"UPDATE video_jobs SET {cols} WHERE id = ?", values)


def delete_job(job_id):
    with cursor(write=True) as cur:
        cur.execute("DELETE FROM video_jobs WHERE id = ?", (job_id,))


def claim_next_queued_job(worker_name):
    """Atomically lock one queued job for a worker.
    Returns the job dict, or None if nothing is queued.

    This is the only place workers may 'reserve' work — it prevents two
    workers from picking the same job (Rule: Each video is an independent
    job; only one worker may own it at a time)."""
    with cursor(write=True) as cur:
        cur.execute(
            "SELECT id FROM video_jobs WHERE status = 'queued' "
            "ORDER BY created_at ASC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None:
            return None
        job_id = row["id"]
        cur.execute(
            """UPDATE video_jobs
               SET status = 'downloading', locked_by = ?, locked_at = ?, updated_at = ?
               WHERE id = ? AND status = 'queued'""",
            (worker_name, _now(), _now(), job_id),
        )
        if cur.rowcount == 0:
            # another worker grabbed it between SELECT and UPDATE
            return None
        cur.execute("SELECT * FROM video_jobs WHERE id = ?", (job_id,))
        return _row_to_dict(cur.fetchone())


def set_progress(job_id, percent, status=None):
    fields = {"progress_percent": percent, "updated_at": _now()}
    if status:
        fields["status"] = status
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    with cursor(write=True) as cur:
        cur.execute(f"UPDATE video_jobs SET {cols} WHERE id = ?", values)


# =================================================================
# SETTINGS
# =================================================================

def get_setting(key, default=None):
    with cursor() as cur:
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default


def get_all_settings():
    with cursor() as cur:
        cur.execute("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in cur.fetchall()}


def set_setting(key, value):
    with cursor(write=True) as cur:
        cur.execute(
            """INSERT INTO settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, str(value)),
        )


# =================================================================
# COOKIES STATUS
# =================================================================

def get_cookies_status():
    with cursor() as cur:
        cur.execute("SELECT * FROM cookies_status WHERE id = 1")
        return _row_to_dict(cur.fetchone())


def set_cookies_status(synced, cookie_count=0):
    with cursor(write=True) as cur:
        cur.execute(
            """UPDATE cookies_status
               SET synced = ?, synced_at = ?, cookie_count = ?
               WHERE id = 1""",
            (1 if synced else 0, _now() if synced else None, cookie_count),
        )


# =================================================================
# HISTORY
# =================================================================

def get_history(limit=100):
    with cursor() as cur:
        cur.execute("SELECT * FROM history_view LIMIT ?", (limit,))
        return _rows_to_dicts(cur.fetchall())
