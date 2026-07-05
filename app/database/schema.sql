-- YouTube Downloader v7 — Database Schema
-- Single source of truth for all application state.
-- Never store runtime objects; only plain values.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------
-- GROUPS
-- A Group is created only for Playlist / Channel / Search downloads.
-- A Single Video download has no group (group_id = NULL on the job).
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS groups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    type            TEXT NOT NULL CHECK (type IN (
                        'playlist', 'channel-longs', 'channel-shorts',
                        'channel-full', 'search'
                    )),
    name            TEXT NOT NULL,            -- e.g. "Python Playlist", "Fireship"
    source_url      TEXT NOT NULL,            -- original URL user submitted
    folder_path     TEXT,                     -- absolute path on disk, set once known
    total_videos    INTEGER DEFAULT 0,        -- how many videos scanner found
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------
-- VIDEO JOBS
-- Every single video, whether standalone or part of a group,
-- is one independent row/job with its own lifecycle.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS video_jobs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id            INTEGER REFERENCES groups(id) ON DELETE CASCADE,

    -- Metadata (Scanner fills these, never touched by Downloader)
    video_id            TEXT,                 -- YouTube video id (e.g. dQw4w9WgXcQ)
    url                 TEXT NOT NULL,         -- direct watch URL for this video
    original_title      TEXT,                 -- exactly as YouTube provides, never modified
    thumbnail_path       TEXT,                 -- local cache path, e.g. thumbnail_cache/<video_id>.jpg
    duration            INTEGER,               -- seconds
    uploader             TEXT,
    playlist_index       INTEGER,               -- position inside playlist/search/channel, else NULL
    selection_prefix     INTEGER,               -- order the video was selected in the batch popup
                                                  -- (only set when the "add number prefix" toggle is on)

    -- Job control
    format               TEXT NOT NULL DEFAULT 'MP4' CHECK (format IN ('MP3', 'MP4', '3GP')),
    quality              TEXT NOT NULL DEFAULT 'best',   -- e.g. MP4: '1080p'/'720p'/'best'; MP3: '192k'/'320k'; 3GP: '320x240'
    status                TEXT NOT NULL DEFAULT 'queued' CHECK (status IN (
                            'queued', 'downloading', 'converting', 'paused',
                            'skipped', 'completed', 'failed', 'cancelled'
                        )),
    progress_percent      REAL NOT NULL DEFAULT 0,
    used_cookies          INTEGER NOT NULL DEFAULT 0,   -- 0/1 — did this job need cookie retry
    retry_count            INTEGER NOT NULL DEFAULT 0,

    -- Worker locking (so two workers never grab the same job)
    locked_by             TEXT,                 -- worker id/name currently holding the job
    locked_at              TEXT,

    -- Output
    file_path              TEXT,                 -- final saved file
    error_message           TEXT,

    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_video_jobs_status ON video_jobs(status);
CREATE INDEX IF NOT EXISTS idx_video_jobs_group ON video_jobs(group_id);

-- ---------------------------------------------------------------
-- SETTINGS
-- Simple key/value store. Always read through database.py helpers
-- so the rest of the app never deals with raw SQL.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);

INSERT OR IGNORE INTO settings (key, value) VALUES ('concurrent_downloads', '3');
INSERT OR IGNORE INTO settings (key, value) VALUES ('default_format', 'MP4');
INSERT OR IGNORE INTO settings (key, value) VALUES ('downloads_folder', '');
INSERT OR IGNORE INTO settings (key, value) VALUES ('use_cookies_by_default', '0');

-- ---------------------------------------------------------------
-- COOKIES STATUS
-- Tracks whether a synced cookies.txt currently exists.
-- The cookie file itself stays on disk (cookies/cookies.txt);
-- this table only stores metadata about it.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cookies_status (
    id              INTEGER PRIMARY KEY CHECK (id = 1),  -- single row
    synced          INTEGER NOT NULL DEFAULT 0,
    synced_at       TEXT,
    cookie_count    INTEGER DEFAULT 0
);

INSERT OR IGNORE INTO cookies_status (id, synced, synced_at, cookie_count)
VALUES (1, 0, NULL, 0);

-- ---------------------------------------------------------------
-- HISTORY VIEW
-- "History" is not a separate table — it is simply completed/failed
-- jobs. A view keeps that rule enforced in one place.
-- ---------------------------------------------------------------
CREATE VIEW IF NOT EXISTS history_view AS
SELECT
    vj.*,
    g.name AS group_name,
    g.type AS group_type
FROM video_jobs vj
LEFT JOIN groups g ON g.id = vj.group_id
WHERE vj.status IN ('completed', 'failed', 'cancelled')
ORDER BY vj.updated_at DESC;

-- ---------------------------------------------------------------
-- LOCAL BATCH CONVERTER
-- Completely separate from video_jobs — this never touches the
-- download pipeline. Each row is one existing local file being
-- converted to MP3 or 3GP. batch_id groups everything from one
-- "Convert" click so the UI can show/poll just that run.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS local_conversion_jobs (
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
);

CREATE INDEX IF NOT EXISTS idx_local_conv_batch ON local_conversion_jobs(batch_id);

-- ---------------------------------------------------------------
-- PENDING PREVIEWS (Approve tab)
-- A scan of a playlist/search/channel — from the web UI or the browser
-- extension — never auto-queues. It lands here instead, and stays here
-- until the person reviews it in the Approve tab and either confirms
-- (jobs get created, row deleted) or deletes it manually. No auto-expiry.
-- entries_json holds the full scan_preview() entries list (video_id,
-- url, title, thumbnail_path, duration, uploader, playlist_index).
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pending_previews (
    id              TEXT PRIMARY KEY,       -- uuid
    type            TEXT NOT NULL,          -- playlist / search / channel-longs / channel-shorts / channel-full
    group_name      TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    format          TEXT NOT NULL DEFAULT 'MP4',
    quality         TEXT NOT NULL DEFAULT 'best',
    video_count     INTEGER NOT NULL DEFAULT 0,
    entries_json    TEXT NOT NULL,          -- JSON list, see scanner.scan_preview()
    scan_status     TEXT NOT NULL DEFAULT 'complete' CHECK (scan_status IN ('scanning', 'complete')),
    source          TEXT NOT NULL DEFAULT 'web',  -- 'web' or 'extension' — cosmetic, shown in Approve tab
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
