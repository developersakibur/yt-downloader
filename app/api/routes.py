# app/api/routes.py
"""
Web API.

Rule: UI never calls yt-dlp directly. Every action the UI can take
(scan a URL, pause/resume/retry/skip/delete a job, change settings,
sync/clear cookies, view history) goes through one of these endpoints,
which in turn only ever talks to database.py / queue.py / scanner.py /
cookies.py. The API layer holds no application state of its own beyond
tracking in-flight scan requests (the scan *results* land in the
database immediately, same as everywhere else).
"""

import os
import sys
import json
import uuid
import threading

from flask import Blueprint, request, jsonify

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
for sub in ("database", "cookies", "downloader", "scanner", "converter"):
    sys.path.insert(0, os.path.join(_APP_DIR, sub))

import database as db    # noqa: E402
import cookies as ck     # noqa: E402
import job_queue as q        # noqa: E402
import scanner            # noqa: E402
import converter           # noqa: E402
import glob                # noqa: E402
from logging_setup import get_logger, _LOG_FILE  # noqa: E402

log = get_logger("routes")

api = Blueprint("api", __name__, url_prefix="/api")

# ---------------------------------------------------------------
# In-memory tracking for in-flight scan requests only (transient
# coordination, not application state — the moment a scan finishes,
# its real output already lives in groups/video_jobs).
# ---------------------------------------------------------------
_scan_registry = {}
_scan_lock = threading.Lock()


# Valid quality values per format — mirrors worker.py's QUALITY_MAP keys
# so an invalid/stale value from an old client never reaches yt-dlp/ffmpeg.
_VALID_QUALITIES = {
    "MP4": ("best", "2160p", "1440p", "1080p", "720p", "480p", "360p", "240p"),
    "MP3": ("192k", "320k", "256k", "128k", "96k"),
    "3GP": ("320x240", "352x288", "176x144"),
}


def _run_scan(scan_id, url, quantity, force_playlist, format="MP4", quality="best"):
    try:
        result = scanner.scan(url, quantity=quantity, force_playlist=force_playlist,
                               format=format, quality=quality)
        with _scan_lock:
            _scan_registry[scan_id] = {"status": "done", "result": result, "error": None}
    except Exception as e:
        with _scan_lock:
            _scan_registry[scan_id] = {"status": "error", "result": None, "error": str(e)}


# ---------------------------------------------------------------
# SCANNER API
# ---------------------------------------------------------------

@api.route("/scan", methods=["POST"])
def post_scan():
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    quantity = data.get("quantity", 25)
    force_playlist = bool(data.get("playlist", False))
    format = (data.get("format") or "MP4").upper()
    if format not in ("MP4", "MP3", "3GP"):
        format = "MP4"

    quality = data.get("quality") or ("best" if format == "MP4" else _VALID_QUALITIES[format][0])
    if quality not in _VALID_QUALITIES[format]:
        quality = _VALID_QUALITIES[format][0]

    if not url:
        return jsonify({"ok": False, "error": "url required"}), 400
    if not scanner.is_valid_youtube_url(url):
        return jsonify({"ok": False, "error": "not a valid YouTube URL"}), 400

    scan_id = str(uuid.uuid4())
    with _scan_lock:
        _scan_registry[scan_id] = {"status": "pending", "result": None, "error": None}

    threading.Thread(target=_run_scan, args=(scan_id, url, quantity, force_playlist, format, quality), daemon=True).start()
    return jsonify({"ok": True, "scan_id": scan_id})


def _run_preview(scan_id, preview_id, url, quantity, force_playlist, format, quality, source):
    def on_progress(fetched, total):
        with _scan_lock:
            entry = _scan_registry.get(scan_id)
            if entry is not None:
                entry["progress"] = {"fetched": fetched, "total": total}

    def on_entries(new_entries):
        # Called once per video as its metadata+thumbnail become ready —
        # push it into the DB immediately so the Approve tab can show it
        # right away instead of waiting for the whole scan to finish.
        db.append_preview_entries(preview_id, new_entries)

    try:
        result = scanner.scan_preview(url, quantity=quantity, force_playlist=force_playlist,
                                       on_progress=on_progress, on_entries=on_entries)
        db.finish_preview_scan(preview_id, group_name=result["group_name"])
        with _scan_lock:
            _scan_registry[scan_id] = {
                "status": "done",
                "result": {"preview_id": preview_id, "video_count": len(result["entries"]),
                       "stopped_early": result.get("stopped_early", False)},
                "error": None,
                "progress": _scan_registry.get(scan_id, {}).get("progress"),
            }
    except Exception as e:
        # Scan failed partway — whatever entries already streamed into
        # the preview stay there (partial-approve is allowed), we just
        # flip it out of 'scanning' so the UI stops waiting for more.
        log.exception(f"preview scan failed for {url}")
        db.finish_preview_scan(preview_id)
        with _scan_lock:
            _scan_registry[scan_id] = {"status": "error", "result": None, "error": str(e), "progress": None}


@api.route("/scan/preview", methods=["POST"])
def post_scan_preview():
    """Group-type URLs (playlist/search/channel) only — from either the
    web UI or the browser extension. Never auto-queues: scans, then
    stores the result as a pending preview for the Approve tab.
    Single video/short should keep using POST /api/scan as before."""
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    quantity = data.get("quantity", 25)
    force_playlist = bool(data.get("playlist", False))
    format = (data.get("format") or "MP4").upper()
    if format not in ("MP4", "MP3", "3GP"):
        format = "MP4"
    quality = data.get("quality") or ("best" if format == "MP4" else _VALID_QUALITIES[format][0])
    if quality not in _VALID_QUALITIES[format]:
        quality = _VALID_QUALITIES[format][0]
    source = data.get("source") if data.get("source") in ("web", "extension") else "web"

    if not url:
        return jsonify({"ok": False, "error": "url required"}), 400
    if not scanner.is_valid_youtube_url(url):
        return jsonify({"ok": False, "error": "not a valid YouTube URL"}), 400

    url_type = scanner.detect_type(url, force_playlist=force_playlist)
    if url_type == "unknown":
        return jsonify({"ok": False, "error": f"Could not recognize URL type: {url}"}), 400

    scan_id = str(uuid.uuid4())
    preview_id = str(uuid.uuid4())
    with _scan_lock:
        _scan_registry[scan_id] = {"status": "pending", "result": None, "error": None, "progress": None}

    # Create the preview row NOW, before any scanning happens — entries
    # stream into it one by one as they're found (see _run_preview), so
    # the Approve tab can show + let the person start selecting videos
    # while the scan is still in progress, not just once it's all done.
    db.create_streaming_preview(
        id_=preview_id,
        type_=url_type,
        group_name=scanner._group_name_for(url_type),
        source_url=url,
        format=format,
        quality=quality,
        source=source,
    )

    threading.Thread(
        target=_run_preview,
        args=(scan_id, preview_id, url, quantity, force_playlist, format, quality, source),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "scan_id": scan_id, "preview_id": preview_id})


@api.route("/scan/status/<scan_id>")
def get_scan_status(scan_id):
    with _scan_lock:
        entry = _scan_registry.get(scan_id)
    if entry is None:
        return jsonify({"ok": False, "error": "unknown scan_id"}), 404
    return jsonify({"ok": True, **entry})


@api.route("/scan/count")
def get_scan_count():
    """Fast video-count lookup — used for the 'Whole playlist' scope
    option on a video+list URL (web + extension), so the person sees how
    many videos are actually in it before committing to a full scan."""
    url = (request.args.get("url") or "").strip()
    force_playlist = request.args.get("playlist", "false").lower() == "true"
    if not url:
        return jsonify({"ok": False, "error": "url required"}), 400
    if not scanner.is_valid_youtube_url(url):
        return jsonify({"ok": False, "error": "not a valid YouTube URL"}), 400
    try:
        result = scanner.quick_count(url, force_playlist=force_playlist)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ---------------------------------------------------------------
# GHOST HISTORY CLEANUP
# Completed jobs whose file was deleted outside the app (e.g. the whole
# Downloads folder wiped) still linger as DB rows/UI entries otherwise.
# Runs on-fetch (queue + history) rather than only at startup, so it
# also catches files removed while the app is running.
# ---------------------------------------------------------------

def _prune_ghost_jobs():
    for job in db.list_completed_jobs_for_prune():
        file_path = job.get("file_path")
        if file_path and os.path.isfile(file_path):
            continue  # file still exists — not a ghost

        thumb = job.get("thumbnail_path")
        if thumb and os.path.isfile(thumb):
            try:
                os.remove(thumb)
            except OSError:
                pass  # non-fatal — DB row cleanup still proceeds

        db.delete_job(job["id"])


# ---------------------------------------------------------------
# APPROVE TAB — pending previews from web UI or extension scans.
# Nothing here ever auto-downloads; a preview sits until the person
# confirms (selected entries -> Group + Jobs, row deleted) or deletes
# it manually. No auto-expiry.
# ---------------------------------------------------------------

@api.route("/previews")
def get_previews():
    return jsonify({"ok": True, "previews": db.list_pending_previews()})


@api.route("/previews/<preview_id>")
def get_preview(preview_id):
    row = db.get_pending_preview(preview_id)
    if row is None:
        return jsonify({"ok": False, "error": "preview not found"}), 404
    row["entries"] = json.loads(row.pop("entries_json"))
    return jsonify({"ok": True, "preview": row})


@api.route("/previews/<preview_id>", methods=["DELETE"])
def delete_preview(preview_id):
    row = db.get_pending_preview(preview_id)
    if row is None:
        return jsonify({"ok": False, "error": "preview not found"}), 404
    db.delete_pending_preview(preview_id)
    return jsonify({"ok": True})


@api.route("/previews/<preview_id>/confirm", methods=["POST"])
def confirm_preview(preview_id):
    """Creates the Group + Jobs for a selection made in the Approve tab.
    type/group_name/source_url are trusted from the stored preview row
    (not the client) — only the selected entries (with prefix) and an
    optional format/quality override come from the request body."""
    row = db.get_pending_preview(preview_id)
    if row is None:
        return jsonify({"ok": False, "error": "preview not found"}), 404

    data = request.get_json(force=True) or {}
    entries = data.get("entries") or []

    format = (data.get("format") or row["format"]).upper()
    if format not in ("MP4", "MP3", "3GP"):
        format = row["format"]
    quality = data.get("quality") or row["quality"]
    if quality not in _VALID_QUALITIES[format]:
        quality = _VALID_QUALITIES[format][0]

    try:
        result = scanner.confirm_batch(
            source_url=row["source_url"], url_type=row["type"], group_name=row["group_name"],
            entries=entries, format=format, quality=quality,
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    db.delete_pending_preview(preview_id)
    return jsonify({"ok": True, **result})


# ---------------------------------------------------------------
# QUEUE API
# ---------------------------------------------------------------

_VALID_ACTIONS = {
    "pause": q.pause_job,
    "resume": q.resume_job,
    "retry": q.retry_job,
    "skip": q.skip_job,
    "cancel": q.cancel_job,
}


# Statuses shown in the Queue (active/pending/failed = user needs to act)
# Completed, cancelled, skipped → History only
_QUEUE_STATUSES = ["queued", "downloading", "converting", "paused", "failed"]

@api.route("/queue/pause-all", methods=["POST"])
def pause_all_jobs():
    return jsonify({"ok": True, "paused": q.pause_all()})


@api.route("/queue/resume-all", methods=["POST"])
def resume_all_jobs():
    return jsonify({"ok": True, "resumed": q.resume_all()})


@api.route("/queue")
def get_queue():
    _prune_ghost_jobs()
    status = request.args.get("status")
    group_id = request.args.get("group_id", type=int)
    if status:
        statuses = status.split(",")
    else:
        statuses = _QUEUE_STATUSES  # never show completed in queue
    jobs = db.list_jobs(status=statuses, group_id=group_id)
    return jsonify({"ok": True, "jobs": jobs, "stats": q.queue_stats()})


@api.route("/queue/<int:job_id>", methods=["PATCH"])
def patch_queue_job(job_id):
    data = request.get_json(force=True) or {}
    action = data.get("action")
    if action not in _VALID_ACTIONS:
        return jsonify({"ok": False, "error": f"action must be one of {sorted(_VALID_ACTIONS)}"}), 400
    try:
        job = _VALID_ACTIONS[action](job_id)
        return jsonify({"ok": True, "job": job})
    except q.InvalidTransition as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 404


@api.route("/queue/<int:job_id>", methods=["DELETE"])
def delete_queue_job(job_id):
    delete_file = request.args.get("delete_file", "false").lower() == "true"
    job = db.get_job(job_id)
    if job is None:
        return jsonify({"ok": False, "error": "job not found"}), 404
    if delete_file and job.get("file_path") and os.path.isfile(job["file_path"]):
        try:
            os.remove(job["file_path"])
        except OSError as e:
            return jsonify({"ok": False, "error": f"could not delete file: {e}"}), 500
    # Fix 8: always delete thumbnail from cache when removing a job
    thumb = job.get("thumbnail_path")
    if thumb and os.path.isfile(thumb):
        try:
            os.remove(thumb)
        except OSError:
            pass  # non-fatal
    q.delete_job(job_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------
# GROUPS API (used by UI for group dropdowns / per-group progress)
# ---------------------------------------------------------------

@api.route("/groups")
def get_groups():
    groups = db.list_groups()
    for g in groups:
        jobs = db.list_jobs(group_id=g["id"])
        g["job_count"] = len(jobs)
        g["completed_count"] = len([j for j in jobs if j["status"] == "completed"])
    return jsonify({"ok": True, "groups": groups})


@api.route("/groups/<int:group_id>")
def get_group(group_id):
    group = db.get_group(group_id)
    if group is None:
        return jsonify({"ok": False, "error": "group not found"}), 404
    group["jobs"] = db.list_jobs(group_id=group_id)
    return jsonify({"ok": True, "group": group})


# ---------------------------------------------------------------
# SETTINGS API
# ---------------------------------------------------------------

_SETTINGS_VALIDATORS = {
    "concurrent_downloads": lambda v: 1 <= int(v) <= 10,
    "converter_concurrency": lambda v: 1 <= int(v) <= 4,
    "default_format": lambda v: v.upper() in ("MP3", "MP4", "3GP"),
    "use_cookies_by_default": lambda v: str(v).lower() in ("0", "1", "true", "false"),
    "downloads_folder": lambda v: v == "" or os.path.isabs(v),  # empty = use default
}


@api.route("/settings")
def get_settings():
    return jsonify({"ok": True, "settings": db.get_all_settings()})


@api.route("/settings", methods=["PUT"])
def put_settings():
    data = request.get_json(force=True) or {}
    errors = {}
    for key, value in data.items():
        validator = _SETTINGS_VALIDATORS.get(key)
        if validator and not validator(value):
            errors[key] = "invalid value"
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    for key, value in data.items():
        db.set_setting(key, value)

    if "concurrent_downloads" in data:
        try:
            import app as _app_module  # lazy import: app.py imports this module, so
                                        # importing at call-time (not module load) avoids a cycle
            _app_module.resize_worker_pool(data["concurrent_downloads"])
        except Exception:
            pass  # non-fatal — new limit still takes effect on next restart

    return jsonify({"ok": True, "settings": db.get_all_settings()})


# ---------------------------------------------------------------
# COOKIES API
# ---------------------------------------------------------------

@api.route("/cookies/status")
def get_cookies_status():
    return jsonify({"ok": True, **ck.status()})


@api.route("/cookies", methods=["POST"])
def post_cookies():
    data = request.get_json(force=True) or {}
    cookie_list = data.get("cookies", [])
    if not cookie_list:
        return jsonify({"ok": False, "error": "no cookies received"}), 400
    try:
        count = ck.sync_cookies_from_browser(cookie_list)
        return jsonify({"ok": True, "count": count})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@api.route("/cookies", methods=["DELETE"])
def delete_cookies():
    ck.delete_cookies()
    return jsonify({"ok": True})


# ---------------------------------------------------------------
# HISTORY API
# ---------------------------------------------------------------

@api.route("/history")
def get_history():
    _prune_ghost_jobs()
    limit = request.args.get("limit", default=100, type=int)
    search = request.args.get("search", default="", type=str).strip()
    status = request.args.get("status", default="", type=str).strip()  # completed/failed/cancelled
    format_ = request.args.get("format", default="", type=str).strip().upper()
    return jsonify({"ok": True, "history": db.get_history(
        limit=limit, search=search or None, status=status or None, format=format_ or None
    )})


# ---------------------------------------------------------------
# STATS (small dashboard summary, used by Web UI header)
# ---------------------------------------------------------------

@api.route("/stats")
def get_stats():
    stats = q.queue_stats()
    return jsonify({"ok": True, "stats": stats})


@api.route("/status")
def get_status():
    return jsonify({"ok": True})


@api.route("/logs")
def get_logs():
    """Tail the app.log file for the Logs tab. ?lines=N caps how many
    of the most recent lines are returned (default 1000) — the file
    itself can be rotated up to 5x5MB, way too much to ship to the
    browser in one response."""
    try:
        lines = max(1, min(int(request.args.get("lines", 1000)), 5000))
    except ValueError:
        lines = 1000

    if not os.path.exists(_LOG_FILE):
        return jsonify({"ok": True, "text": "", "size": 0, "total_lines": 0, "truncated": False})

    size = os.path.getsize(_LOG_FILE)
    with open(_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    tail = all_lines[-lines:]
    return jsonify({
        "ok": True,
        "text": "".join(tail),
        "size": size,
        "total_lines": len(all_lines),
        "truncated": len(all_lines) > len(tail),
    })


@api.route("/logs", methods=["DELETE"])
def clear_logs():
    """Empty app.log and remove any rotated backups (app.log.1 .. .5)
    so 'Clear' actually frees the disk space, not just hides the tail."""
    try:
        if os.path.exists(_LOG_FILE):
            open(_LOG_FILE, "w", encoding="utf-8").close()
        for backup in glob.glob(_LOG_FILE + ".*"):
            os.remove(backup)
        log.info("log file cleared via UI")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------
# LOCAL BATCH CONVERTER
# Separate from the download pipeline entirely — see converter.py.
# ---------------------------------------------------------------

_CONVERT_QUALITIES = {
    "MP3": ("192k", "320k", "256k", "128k", "96k"),
    "3GP": ("320x240", "352x288", "176x144"),
}


@api.route("/local-convert", methods=["POST"])
def start_local_convert():
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip()
    target_format = (data.get("target_format") or "").upper()
    recursive = bool(data.get("recursive", False))

    if not path:
        return jsonify({"ok": False, "error": "path required"}), 400
    if target_format not in _CONVERT_QUALITIES:
        return jsonify({"ok": False, "error": "target_format must be MP3 or 3GP"}), 400
    if not os.path.isdir(path):
        return jsonify({"ok": False, "error": f"Not a folder: {path}"}), 400

    quality = data.get("quality") or _CONVERT_QUALITIES[target_format][0]
    if quality not in _CONVERT_QUALITIES[target_format]:
        quality = _CONVERT_QUALITIES[target_format][0]

    # Concurrency: explicit per-request value wins, otherwise fall back
    # to the person's saved default (Settings tab), otherwise 2.
    concurrency = data.get("concurrency")
    if concurrency is None:
        concurrency = int(db.get_setting("converter_concurrency", 2))
    try:
        concurrency = max(1, min(int(concurrency), 4))
    except (TypeError, ValueError):
        concurrency = 2

    try:
        result = converter.start_batch(path, target_format, quality, recursive=recursive, concurrency=concurrency)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not start conversion: {e}"}), 500

    return jsonify({"ok": True, **result})


@api.route("/local-convert/jobs")
def get_local_convert_jobs():
    batch_id = request.args.get("batch_id", "")
    if not batch_id:
        return jsonify({"ok": False, "error": "batch_id required"}), 400
    return jsonify({"ok": True, "jobs": converter.list_batch_jobs(batch_id)})


_CONVERT_ACTIONS = {
    "pause": converter.pause_job,
    "resume": converter.resume_job,
    "skip": converter.skip_job,
}


@api.route("/local-convert/jobs/<int:job_id>", methods=["PATCH"])
def patch_local_convert_job(job_id):
    data = request.get_json(force=True) or {}
    action = data.get("action")
    if action not in _CONVERT_ACTIONS:
        return jsonify({"ok": False, "error": f"action must be one of {sorted(_CONVERT_ACTIONS)}"}), 400
    try:
        job = _CONVERT_ACTIONS[action](job_id)
        return jsonify({"ok": True, "job": job})
    except converter.InvalidTransition as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 404


@api.route("/local-convert/jobs/<int:job_id>", methods=["DELETE"])
def delete_local_convert_job(job_id):
    try:
        converter.delete_job(job_id)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    return jsonify({"ok": True})
