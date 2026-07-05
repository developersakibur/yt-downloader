# app/converter/converter.py
"""
Local Batch Converter — Rule: never touches the download pipeline.

Converts video files that already exist on disk to MP3 or 3GP. Runs on
its own table (local_conversion_jobs), never touches video_jobs, never
downloads anything from YouTube. Each "Convert" click creates one
batch_id and a small fixed pool of worker threads scoped to just that
batch — they claim queued rows until none are left, then exit. No
persistent pool to start/stop with the app lifecycle.
"""

import os
import sys
import threading
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloader"))
import database as db     # noqa: E402
import worker as _worker  # noqa: E402  — reused for ffmpeg_convert, sanitize_name, quality helpers

ALLOWED_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".3gp"}

# Our own output subfolders — never scan into these (would otherwise
# re-convert our own output on a repeat run, or loop on itself).
OUTPUT_SUBFOLDER_NAMES = {"MP3", "3GP"}

# ---------------------------------------------------------------
# LIVE PROCESS TRACKING — lets pause/skip/delete reach into an
# in-flight ffmpeg conversion. Windows has no real process pause/resume
# (no SIGSTOP/SIGCONT), so "pause" on a *currently converting* job means:
# kill the ffmpeg process now, discard the partial output, mark the job
# 'paused'. Resuming starts that file over from scratch. Queued jobs are
# unaffected by this limitation — pausing those is just a status flip
# that stops a worker from ever claiming them.
# ---------------------------------------------------------------

_active_processes = {}   # job_id -> subprocess.Popen, only while actually converting
_kill_intent = {}        # job_id -> status to apply once the killed process's exception lands
_process_lock = threading.Lock()

_batch_worker_counts = {}  # batch_id -> number of live worker threads
_batch_lock = threading.Lock()


def _register_process(job_id, proc):
    with _process_lock:
        _active_processes[job_id] = proc


def _unregister_process(job_id):
    with _process_lock:
        _active_processes.pop(job_id, None)
        _kill_intent.pop(job_id, None)


def _kill_running(job_id, target_status):
    """Terminate an in-flight conversion for job_id, if one is running.
    Returns True if a process was found and killed."""
    with _process_lock:
        proc = _active_processes.get(job_id)
        if proc is None or proc.poll() is not None:
            return False
        _kill_intent[job_id] = target_status
    try:
        proc.terminate()
    except Exception:
        pass
    return True


def _cleanup_partial_output(job):
    out = job.get("output_path")
    if out and os.path.exists(out):
        try:
            os.remove(out)
        except OSError:
            pass


def _iter_video_files(root: str, recursive: bool):
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise ValueError(f"Not a folder: {root}")

    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            # prune in-place so os.walk never descends into our own output
            dirnames[:] = [d for d in dirnames if d not in OUTPUT_SUBFOLDER_NAMES]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() in ALLOWED_VIDEO_EXTS:
                    yield os.path.join(dirpath, fn)
    else:
        for fn in os.listdir(root):
            full = os.path.join(root, fn)
            if os.path.isfile(full) and os.path.splitext(fn)[1].lower() in ALLOWED_VIDEO_EXTS:
                yield full


def _output_filename(source_path: str, target_format: str, quality: str) -> str:
    base = _worker.sanitize_name(os.path.splitext(os.path.basename(source_path))[0])
    ext = ".mp3" if target_format == "MP3" else ".3gp"
    # Same quality-suffix convention as regular downloads (G2) — keeps
    # re-runs at a different quality from colliding with earlier output.
    return f"{base} [{quality}]{ext}"


def start_batch(path: str, target_format: str, quality: str,
                 recursive: bool = False, concurrency: int = 2) -> dict:
    """Scan `path` for video files, queue a conversion job per file,
    and kick off worker threads to process them. Returns immediately
    with a batch_id — the caller polls list_batch_jobs() for progress."""
    target_format = target_format.upper()
    if target_format not in ("MP3", "3GP"):
        raise ValueError("target_format must be MP3 or 3GP")

    files = list(_iter_video_files(path, recursive))
    if not files:
        raise ValueError("No video files found in that folder"
                          + (" (including subfolders)" if recursive else ""))

    output_dir = os.path.join(os.path.abspath(path), target_format)
    os.makedirs(output_dir, exist_ok=True)

    batch_id = str(uuid.uuid4())
    queued_count = 0
    skipped_count = 0

    for src in files:
        out_name = _output_filename(src, target_format, quality)
        out_path = os.path.join(output_dir, out_name)
        # Output already exists from a previous run at this quality — skip,
        # never overwrite silently (I5: safe default).
        already_done = os.path.exists(out_path) and os.path.getsize(out_path) > 0
        status = "skipped" if already_done else "queued"
        db.create_local_conversion_job(
            batch_id=batch_id,
            source_path=src,
            source_filename=os.path.basename(src),
            target_format=target_format,
            quality=quality,
            output_path=out_path,
            status=status,
        )
        if already_done:
            skipped_count += 1
        else:
            queued_count += 1

    concurrency = max(1, min(concurrency, 4))
    for _ in range(min(concurrency, queued_count) or 0):
        _spawn_worker(batch_id)

    return {
        "batch_id": batch_id,
        "total": len(files),
        "queued": queued_count,
        "skipped": skipped_count,
    }


def _spawn_worker(batch_id):
    with _batch_lock:
        _batch_worker_counts[batch_id] = _batch_worker_counts.get(batch_id, 0) + 1
    threading.Thread(target=_worker_loop, args=(batch_id,), daemon=True).start()


def list_batch_jobs(batch_id: str) -> list[dict]:
    return db.list_local_conversion_jobs(batch_id)


class InvalidTransition(Exception):
    pass


_ALLOWED_SOURCE = {
    "pause":  {"queued", "converting"},
    "resume": {"paused"},
    "skip":   {"queued", "converting", "paused"},
    "delete": {"queued", "converting", "paused", "completed", "failed", "skipped"},
}


def _job_or_raise(job_id):
    job = db.get_local_conversion_job(job_id)
    if job is None:
        raise ValueError(f"conversion job {job_id} not found")
    return job


def _guard(job, action):
    allowed = _ALLOWED_SOURCE[action]
    if job["status"] not in allowed:
        raise InvalidTransition(
            f"cannot {action} conversion job {job['id']} from status '{job['status']}' "
            f"(allowed from: {sorted(allowed)})"
        )


def pause_job(job_id):
    """Queued: just flips the status so no worker ever claims it.
    Converting: kills the ffmpeg process right now, discards the partial
    output, and marks it 'paused' — resuming re-converts from scratch
    (no real pause/resume on Windows for a running ffmpeg process)."""
    job = _job_or_raise(job_id)
    _guard(job, "pause")
    if job["status"] == "converting":
        _kill_running(job_id, "paused")
        # status gets set to 'paused' by _convert_one's exception handler
        # once the killed process actually raises — but set it here too in
        # case of a race where it's already finished/transitioning.
    else:
        db.update_local_conversion_job(job_id, status="paused")
    return db.get_local_conversion_job(job_id)


def resume_job(job_id):
    """Puts the job back in the queue, and — since a batch's worker
    threads exit once nothing's left queued — spawns a fresh worker for
    this batch if none are currently alive to pick it up."""
    job = _job_or_raise(job_id)
    _guard(job, "resume")
    db.update_local_conversion_job(job_id, status="queued", progress_percent=0)
    with _batch_lock:
        alive = _batch_worker_counts.get(job["batch_id"], 0) > 0
    if not alive:
        _spawn_worker(job["batch_id"])
    return db.get_local_conversion_job(job_id)


def skip_job(job_id):
    """Queued/paused: mark skipped immediately. Converting: kill the
    process now and mark skipped once it unwinds."""
    job = _job_or_raise(job_id)
    _guard(job, "skip")
    if job["status"] == "converting":
        _kill_running(job_id, "skipped")
    else:
        db.update_local_conversion_job(job_id, status="skipped")
    return db.get_local_conversion_job(job_id)


def delete_job(job_id):
    """Removes the row entirely. If it's mid-conversion, kill the process
    first and clean up the partial output before deleting the row —
    _convert_one sees the 'deleted' intent and skips its own DB update
    (the row won't exist anymore by the time it unwinds)."""
    job = _job_or_raise(job_id)
    if job["status"] == "converting":
        _kill_running(job_id, "deleted")
    else:
        _cleanup_partial_output(job)
    db.delete_local_conversion_job(job_id)


def _worker_loop(batch_id: str):
    try:
        while True:
            job = db.claim_next_local_conversion_job(batch_id)
            if job is None:
                return  # nothing left queued in this batch — this worker exits
            _convert_one(job)
    finally:
        with _batch_lock:
            count = _batch_worker_counts.get(batch_id, 1) - 1
            if count <= 0:
                _batch_worker_counts.pop(batch_id, None)
            else:
                _batch_worker_counts[batch_id] = count


def _convert_one(job: dict):
    job_id = job["id"]
    try:
        out_dir = os.path.dirname(job["output_path"])
        os.makedirs(out_dir, exist_ok=True)

        def on_progress(percent):
            db.update_local_conversion_job(job_id, progress_percent=round(min(percent, 99), 1))

        def on_speed(speed_x):
            db.update_local_conversion_job(job_id, conversion_speed_x=speed_x)

        def on_process_start(proc):
            _register_process(job_id, proc)

        if job["target_format"] == "MP3":
            bitrate = _worker.get_mp3_bitrate(job["quality"])
            _worker.ffmpeg_convert(
                [job["source_path"]], job["output_path"],
                ["-c:a", "libmp3lame", "-b:a", bitrate],
                on_progress=on_progress, on_speed=on_speed, on_process_start=on_process_start,
            )
        else:  # 3GP
            resolution, video_bitrate = _worker.get_3gp_profile(job["quality"])
            _worker.ffmpeg_convert(
                [job["source_path"]], job["output_path"],
                ["-s", resolution, "-c:v", "mpeg4", "-b:v", video_bitrate, "-c:a", "aac", "-ac", "1"],
                on_progress=on_progress, on_speed=on_speed, on_process_start=on_process_start,
            )

        db.update_local_conversion_job(job_id, status="completed", progress_percent=100)
    except Exception as e:
        with _process_lock:
            intent = _kill_intent.get(job_id)
        if intent:
            # This "failure" was actually us terminating the process on
            # purpose (pause/skip/delete) — apply the intended status
            # instead of recording it as a real error.
            _cleanup_partial_output(job)
            if intent != "deleted":
                db.update_local_conversion_job(job_id, status=intent, progress_percent=0,
                                                error_message=None, conversion_speed_x=None)
            # 'deleted' intent: row is removed by the route handler itself,
            # nothing left to update here.
        else:
            db.update_local_conversion_job(job_id, status="failed", error_message=str(e))
    finally:
        _unregister_process(job_id)
