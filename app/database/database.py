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
import json
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
        "ALTER TABLE video_jobs ADD COLUMN selection_prefix INTEGER",
        "ALTER TABLE video_jobs ADD COLUMN speed_bytes_sec REAL",
        "ALTER TABLE video_jobs ADD COLUMN eta_seconds INTEGER",
        "ALTER TABLE video_jobs ADD COLUMN conversion_speed_x REAL",
        "ALTER TABLE local_conversion_jobs ADD COLUMN conversion_speed_x REAL",
        "ALTER TABLE pending_previews ADD COLUMN scan_status TEXT NOT NULL DEFAULT 'complete'",
    ]
    _migrate_local_conversion_status(conn)

    for stmt in migrations:
        try:
            conn.execute(stmt)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists — already migrated


def _migrate_local_conversion_status(conn):
    """SQLite can't ALTER a CHECK constraint in place, so if an existing
    local_conversion_jobs table predates the 'paused' status, rebuild it:
    rename -> create new with the wider CHECK -> copy rows -> drop old.
    Safe to call every startup — no-ops once already migrated."""
    cur = conn.cursor()
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='local_conversion_jobs'")
    row = cur.fetchone()
    if row is None or row[0] is None or "'paused'" in row[0]:
        return  # table doesn't exist yet (fresh install) or already migrated
    cur.execute("ALTER TABLE local_conversion_jobs RENAME TO local_conversion_jobs_old")
    cur.execute("""
        CREATE TABLE local_conversion_jobs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id            TEXT NOT NULL,
            source_path         TEXT NOT NULL,
            source_filename     TEXT NOT NULL,
            target_format       TEXT NOT NULL CHECK (target_format IN ('MP3', '3GP')),
            quality             TEXT NOT NULL DEFAULT 'best',
            status              TEXT NOT NULL DEFAULT 'queued' CHECK (status IN (
                                    'queued', 'converting', 'completed', 'failed', 'skipped', 'paused'
                                )),
            progress_percent    REAL NOT NULL DEFAULT 0,
            output_path         TEXT,
            error_message       TEXT,
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    cur.execute("""
        INSERT INTO local_conversion_jobs
            (id, batch_id, source_path, source_filename, target_format, quality,
             status, progress_percent, output_path, error_message, created_at, updated_at)
        SELECT id, batch_id, source_path, source_filename, target_format, quality,
               status, progress_percent, output_path, error_message, created_at, updated_at
        FROM local_conversion_jobs_old
    """)
    cur.execute("DROP TABLE local_conversion_jobs_old")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_local_conv_batch ON local_conversion_jobs(batch_id)")
    conn.commit()


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
                uploader=None, playlist_index=None, selection_prefix=None):
    """Scanner calls this for every video it discovers.
    Job always starts as 'queued'."""
    with cursor(write=True) as cur:
        cur.execute(
            """INSERT INTO video_jobs
               (group_id, video_id, url, original_title, thumbnail_path,
                duration, uploader, playlist_index, selection_prefix,
                format, quality, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued')""",
            (group_id, video_id, url, original_title, thumbnail_path,
             duration, uploader, playlist_index, selection_prefix, format, quality),
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


def list_completed_jobs_for_prune():
    """Completed jobs only — queued/downloading/etc. never have a final
    file yet, so they're excluded from the ghost-file check by design."""
    with cursor() as cur:
        cur.execute(
            "SELECT id, file_path, thumbnail_path FROM video_jobs WHERE status = 'completed'"
        )
        return _rows_to_dicts(cur.fetchall())


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


def set_progress(job_id, percent, status=None, speed_bytes_sec=None, eta_seconds=None):
    fields = {"progress_percent": percent, "updated_at": _now()}
    if status:
        fields["status"] = status
    if speed_bytes_sec is not None:
        fields["speed_bytes_sec"] = speed_bytes_sec
    if eta_seconds is not None:
        fields["eta_seconds"] = eta_seconds
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    with cursor(write=True) as cur:
        cur.execute(f"UPDATE video_jobs SET {cols} WHERE id = ?", values)


def set_conversion_speed(job_id, speed_x):
    with cursor(write=True) as cur:
        cur.execute(
            "UPDATE video_jobs SET conversion_speed_x = ?, updated_at = ? WHERE id = ?",
            (speed_x, _now(), job_id),
        )


def get_total_download_speed():
    """Sum of speed_bytes_sec across all currently-downloading jobs.
    Used for the top-bar 'current total speed' indicator."""
    with cursor() as cur:
        cur.execute(
            "SELECT COALESCE(SUM(speed_bytes_sec), 0) AS total "
            "FROM video_jobs WHERE status = 'downloading' AND speed_bytes_sec IS NOT NULL"
        )
        row = cur.fetchone()
        return row["total"] if row else 0


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


# =================================================================
# LOCAL BATCH CONVERTER
# Separate table, separate lifecycle from video_jobs — see schema.sql.
# =================================================================

def create_local_conversion_job(batch_id, source_path, source_filename,
                                 target_format, quality, output_path, status="queued"):
    with cursor(write=True) as cur:
        cur.execute(
            """INSERT INTO local_conversion_jobs
               (batch_id, source_path, source_filename, target_format,
                quality, output_path, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (batch_id, source_path, source_filename, target_format,
             quality, output_path, status),
        )
        return cur.lastrowid


def claim_next_local_conversion_job(batch_id):
    """Same atomic SELECT-then-UPDATE pattern as claim_next_queued_job()
    for video_jobs — prevents two converter workers grabbing the same
    file. Scoped to one batch so workers from an old batch never pick
    up a newer one's jobs."""
    with cursor(write=True) as cur:
        cur.execute(
            "SELECT id FROM local_conversion_jobs "
            "WHERE batch_id = ? AND status = 'queued' "
            "ORDER BY id ASC LIMIT 1",
            (batch_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        job_id = row["id"]
        cur.execute(
            """UPDATE local_conversion_jobs
               SET status = 'converting', updated_at = ?
               WHERE id = ? AND status = 'queued'""",
            (_now(), job_id),
        )
        if cur.rowcount == 0:
            return None  # another worker grabbed it first
        cur.execute("SELECT * FROM local_conversion_jobs WHERE id = ?", (job_id,))
        return _row_to_dict(cur.fetchone())


def update_local_conversion_job(job_id, **fields):
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [job_id]
    with cursor(write=True) as cur:
        cur.execute(f"UPDATE local_conversion_jobs SET {cols} WHERE id = ?", values)


def list_local_conversion_jobs(batch_id):
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM local_conversion_jobs WHERE batch_id = ? ORDER BY id ASC",
            (batch_id,),
        )
        return _rows_to_dicts(cur.fetchall())


def get_local_conversion_job(job_id):
    with cursor() as cur:
        cur.execute("SELECT * FROM local_conversion_jobs WHERE id = ?", (job_id,))
        return _row_to_dict(cur.fetchone())


def delete_local_conversion_job(job_id):
    with cursor(write=True) as cur:
        cur.execute("DELETE FROM local_conversion_jobs WHERE id = ?", (job_id,))


# =================================================================
# PENDING PREVIEWS (Approve tab)
# =================================================================

def create_pending_preview(id_, type_, group_name, source_url, format, quality,
                            video_count, entries_json, source="web"):
    with cursor(write=True) as cur:
        cur.execute(
            """INSERT INTO pending_previews
               (id, type, group_name, source_url, format, quality,
                video_count, entries_json, scan_status, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'complete', ?)""",
            (id_, type_, group_name, source_url, format, quality,
             video_count, entries_json, source),
        )
        return id_


def create_streaming_preview(id_, type_, group_name, source_url, format, quality, source="web"):
    """Insert a preview row immediately, before the scan has actually
    fetched any entries yet — status 'scanning'. append_preview_entries()
    fills it in as results come in, so the Approve tab can show the card
    (and let the person start selecting/confirming) while the scan is
    still running, instead of waiting for the whole thing to finish."""
    with cursor(write=True) as cur:
        cur.execute(
            """INSERT INTO pending_previews
               (id, type, group_name, source_url, format, quality,
                video_count, entries_json, scan_status, source)
               VALUES (?, ?, ?, ?, ?, ?, 0, '[]', 'scanning', ?)""",
            (id_, type_, group_name, source_url, format, quality, source),
        )
        return id_


def append_preview_entries(id_, new_entries: list):
    """Appends new_entries onto an existing preview's entries_json and
    bumps video_count to match. Called repeatedly (once per video, or in
    small batches) while a streaming scan is still running."""
    if not new_entries:
        return
    with cursor(write=True) as cur:
        cur.execute("SELECT entries_json FROM pending_previews WHERE id = ?", (id_,))
        row = cur.fetchone()
        if row is None:
            return  # preview was deleted (e.g. user cancelled) — drop silently
        current = json.loads(row["entries_json"])
        current.extend(new_entries)
        cur.execute(
            "UPDATE pending_previews SET entries_json = ?, video_count = ? WHERE id = ?",
            (json.dumps(current), len(current), id_),
        )


def finish_preview_scan(id_, group_name=None):
    """Marks a streaming preview's scan as complete. group_name is passed
    if it was only discoverable partway through the scan (e.g. playlist
    title from the first chunk) and wasn't known at creation time."""
    with cursor(write=True) as cur:
        if group_name:
            cur.execute(
                "UPDATE pending_previews SET scan_status = 'complete', group_name = ? WHERE id = ?",
                (group_name, id_),
            )
        else:
            cur.execute(
                "UPDATE pending_previews SET scan_status = 'complete' WHERE id = ?", (id_,)
            )


def list_pending_previews():
    """Summary rows only (no entries_json) — for the collapsed Approve
    tab accordion list."""
    with cursor() as cur:
        cur.execute(
            "SELECT id, type, group_name, source_url, format, quality, "
            "video_count, scan_status, source, created_at FROM pending_previews "
            "ORDER BY created_at DESC"
        )
        return _rows_to_dicts(cur.fetchall())


def get_pending_preview(id_):
    """Full row, including entries_json — used when an accordion item
    is expanded, or on confirm."""
    with cursor() as cur:
        cur.execute("SELECT * FROM pending_previews WHERE id = ?", (id_,))
        return _row_to_dict(cur.fetchone())


def delete_pending_preview(id_):
    with cursor(write=True) as cur:
        cur.execute("DELETE FROM pending_previews WHERE id = ?", (id_,))
