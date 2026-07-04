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


def _run_preview(scan_id, url, quantity, force_playlist, format, quality, source):
    try:
        result = scanner.scan_preview(url, quantity=quantity, force_playlist=force_playlist)
        preview_id = str(uuid.uuid4())
        db.create_pending_preview(
            id_=preview_id,
            type_=result["type"],
            group_name=result["group_name"],
            source_url=result["source_url"],
            format=format,
            quality=quality,
            video_count=len(result["entries"]),
            entries_json=json.dumps(result["entries"]),
            source=source,
        )
        with _scan_lock:
            _scan_registry[scan_id] = {
                "status": "done",
                "result": {"preview_id": preview_id, "video_count": len(result["entries"])},
                "error": None,
            }
    except Exception as e:
        with _scan_lock:
            _scan_registry[scan_id] = {"status": "error", "result": None, "error": str(e)}


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

    scan_id = str(uuid.uuid4())
    with _scan_lock:
        _scan_registry[scan_id] = {"status": "pending", "result": None, "error": None}

    threading.Thread(
        target=_run_preview,
        args=(scan_id, url, quantity, force_playlist, format, quality, source),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "scan_id": scan_id})


@api.route("/scan/status/<scan_id>")
def get_scan_status(scan_id):
    with _scan_lock:
        entry = _scan_registry.get(scan_id)
    if entry is None:
        return jsonify({"ok": False, "error": "unknown scan_id"}), 404
    return jsonify({"ok": True, **entry})


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
    return jsonify({"ok": True, "history": db.get_history(limit=limit)})


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

    try:
        result = converter.start_batch(path, target_format, quality, recursive=recursive)
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
