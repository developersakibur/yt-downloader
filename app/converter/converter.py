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
        threading.Thread(target=_worker_loop, args=(batch_id,), daemon=True).start()

    return {
        "batch_id": batch_id,
        "total": len(files),
        "queued": queued_count,
        "skipped": skipped_count,
    }


def list_batch_jobs(batch_id: str) -> list[dict]:
    return db.list_local_conversion_jobs(batch_id)


def _worker_loop(batch_id: str):
    while True:
        job = db.claim_next_local_conversion_job(batch_id)
        if job is None:
            return  # nothing left queued in this batch — this worker exits
        _convert_one(job)


def _convert_one(job: dict):
    job_id = job["id"]
    try:
        out_dir = os.path.dirname(job["output_path"])
        os.makedirs(out_dir, exist_ok=True)

        def on_progress(percent):
            db.update_local_conversion_job(job_id, progress_percent=round(min(percent, 99), 1))

        if job["target_format"] == "MP3":
            bitrate = _worker.get_mp3_bitrate(job["quality"])
            _worker.ffmpeg_convert(
                [job["source_path"]], job["output_path"],
                ["-c:a", "libmp3lame", "-b:a", bitrate],
                on_progress=on_progress,
            )
        else:  # 3GP
            resolution, video_bitrate = _worker.get_3gp_profile(job["quality"])
            _worker.ffmpeg_convert(
                [job["source_path"]], job["output_path"],
                ["-s", resolution, "-c:v", "mpeg4", "-b:v", video_bitrate, "-c:a", "aac", "-ac", "1"],
                on_progress=on_progress,
            )

        db.update_local_conversion_job(job_id, status="completed", progress_percent=100)
    except Exception as e:
        db.update_local_conversion_job(job_id, status="failed", error_message=str(e))
