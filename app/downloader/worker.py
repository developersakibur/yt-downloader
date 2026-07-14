# app/downloader/worker.py
"""
Download Worker.

Rule 1: Downloader NEVER scans (no metadata discovery here — that's
        Scanner's job, already done before a job reaches 'queued').
Rule: Convert is part of the Download Job — there is no separate
      Convert Engine. yt-dlp -> ffmpeg -> merge -> save -> completed,
      all inside one job's lifecycle.

Workers continuously monitor the Queue (never call a "download_video()"
directly from outside). Worker count is configurable via Settings.
"""

import os
import re
import sys
import time
import shutil
import threading
import subprocess
import unicodedata

import warnings
warnings.filterwarnings("ignore")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_THIS_DIR), "database"))
sys.path.insert(0, os.path.join(os.path.dirname(_THIS_DIR), "cookies"))
sys.path.insert(0, os.path.dirname(_THIS_DIR))
import database as db   # noqa: E402
from errors import clean_error_text  # noqa: E402
import cookies as ck    # noqa: E402
import job_queue as q        # noqa: E402
from logging_setup import get_logger  # noqa: E402

log = get_logger("worker")

BASE_DIR = os.path.dirname(os.path.dirname(_THIS_DIR))


def _windows_downloads_folder():
    """Resolve the real Windows 'Downloads' folder for the current user,
    respecting the case where they've relocated it (e.g. to another
    drive) via the Known Folder API — falls back to the standard
    ~/Downloads path if that lookup isn't available (non-Windows, the
    API call fails, or it returns something that doesn't look like a
    real Downloads folder)."""
    fallback = os.path.join(os.path.expanduser("~"), "Downloads")

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            FOLDERID_Downloads = "{374DE290-123F-4565-9164-39C4925E467B}"
            guid = ctypes.create_unicode_buffer(FOLDERID_Downloads)
            buf = ctypes.c_wchar_p()

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]

            rfid = GUID()
            hr1 = ctypes.windll.ole32.CLSIDFromString(guid, ctypes.byref(rfid))
            if hr1 != 0:
                log.warning(f"CLSIDFromString failed (hr={hr1:#x}); using ~/Downloads")
                return fallback

            hr2 = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(rfid), 0, 0, ctypes.byref(buf)
            )
            try:
                if hr2 != 0 or not buf.value:
                    log.warning(f"SHGetKnownFolderPath failed (hr={hr2:#x}); using ~/Downloads")
                    return fallback

                resolved = buf.value
                # Sanity check: a real Downloads folder is never the bare
                # profile root or a drive root — reject anything that looks
                # like that instead of quietly writing into it later.
                home = os.path.expanduser("~")
                if os.path.normcase(os.path.normpath(resolved)) in (
                    os.path.normcase(os.path.normpath(home)),
                    os.path.normcase(os.path.splitdrive(resolved)[0] + "\\"),
                ):
                    log.warning(
                        f"SHGetKnownFolderPath returned a suspicious path "
                        f"({resolved!r}); using ~/Downloads instead"
                    )
                    return fallback

                return resolved
            finally:
                # buf.value was allocated by SHGetKnownFolderPath via
                # CoTaskMemAlloc — free it so we don't leak on every call.
                if buf:
                    ctypes.windll.ole32.CoTaskMemFree(buf)
        except Exception:
            log.exception("Windows Downloads-folder lookup failed; using ~/Downloads")

    return fallback


_DEFAULT_DOWNLOADS = os.path.join(_windows_downloads_folder(), "YT Downloader")
log.info(f"Downloads root resolved to: {_DEFAULT_DOWNLOADS}")


def set_hidden(path: str):
    """Apply the Windows hidden attribute to a folder (no-op elsewhere,
    and non-fatal if it fails — hidden is cosmetic, never blocks a save)."""
    if sys.platform == "win32" and os.path.isdir(path):
        try:
            subprocess.run(["attrib", "+h", path], shell=True, check=False,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

def get_downloads_root():
    """Read from settings every time so user changes take effect immediately."""
    path = db.get_setting("downloads_folder", "").strip()
    return path if path and os.path.isabs(path) else _DEFAULT_DOWNLOADS


class SilentLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


# ---------------------------------------------------------------
# FILE / FOLDER NAMING
# ---------------------------------------------------------------

def sanitize_name(name: str) -> str:
    """Remove emoji and Windows-invalid characters only.
    Never otherwise modify the title (File Naming rule)."""
    name = (name or "").strip()
    # strip emoji / non-BMP symbols
    name = "".join(ch for ch in name if unicodedata.category(ch) not in ("So", "Cn"))
    # strip Windows-invalid filename characters
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    return name.strip() or "untitled"


def ensure_group_folder(job: dict) -> str:
    """Return the final save folder for a job, creating it if needed.

    Structure (flat — no nested subfolders):
        <downloads_root>/MP4/                    ← single video, no group
        <downloads_root>/MP3/
        <downloads_root>/3GP/
        <downloads_root>/MP4 - <Group Name>/      ← playlist / channel / search
        <downloads_root>/MP3 - <Group Name>/
        <downloads_root>/3GP - <Group Name>/

    Format is baked into the folder name itself (not a subfolder), so
    downloading the same playlist in three formats produces three
    separate flat folders side by side in the root — never one folder
    containing three format subfolders. This is safe because every
    scan() call creates its own Group row with a single format that
    never changes for that group's jobs.
    """
    fmt = job.get("format", "MP4").upper()
    root = get_downloads_root()

    if not job.get("group_id"):
        folder = os.path.join(root, fmt)
        os.makedirs(folder, exist_ok=True)
        return folder

    group = db.get_group(job["group_id"])
    folder = group.get("folder_path")
    if not folder:
        folder = os.path.join(root, f"{fmt} - {sanitize_name(group['name'])}")
        db.update_group(group["id"], folder_path=folder)

    os.makedirs(folder, exist_ok=True)
    return folder


def build_filename(job: dict, ext: str) -> str:
    # Title + quality suffix — always present (even "[best]"), so the
    # same video downloaded at two different qualities never collides
    # on the same filename and silently overwrites one another.
    title = sanitize_name(job.get("original_title") or job.get("video_id") or "video")
    quality = job.get("quality") or "best"
    prefix = job.get("selection_prefix")
    stem = f"{int(prefix):02d} - {title}" if prefix is not None else title
    return f"{stem} [{quality}]{ext}"


# ---------------------------------------------------------------
# FFMPEG
# ---------------------------------------------------------------

def get_ffmpeg_path():
    # 1. System PATH
    found = shutil.which("ffmpeg")
    if found:
        return found
    # 2. Portable folder candidates (tools/ is where PortablePython keeps ffmpeg)
    candidates = (
        "python/ffmpeg/ffmpeg.exe",   # YT_Downloader_v7_Portable/python/ffmpeg/
        "ffmpeg/bin/ffmpeg.exe",
        "ffmpeg/bin/ffmpeg",
        "ffmpeg.exe",
    )
    for candidate in candidates:
        local = os.path.join(BASE_DIR, candidate)
        if os.path.exists(local):
            return local
    return "ffmpeg"


FFMPEG_PATH = get_ffmpeg_path()


# ---------------------------------------------------------------
# QUALITY SYSTEM
# ---------------------------------------------------------------
# Single source of truth for what each quality value means per format.
# routes.py's _VALID_QUALITIES mirrors these keys for request validation;
# if you add a new quality option here, add it there too.

MP4_HEIGHTS = {
    "best": None,   # no cap — take the highest yt-dlp offers
    "2160p": 2160, "1440p": 1440, "1080p": 1080,
    "720p": 720, "480p": 480, "360p": 360, "240p": 240,
}

MP3_BITRATES = {
    "320k": "320k", "256k": "256k", "192k": "192k",
    "128k": "128k", "96k": "96k",
}

# resolution -> (ffmpeg -s value, video bitrate) — bitrate scales down
# with resolution so small/low-motion output isn't needlessly heavy
THREEGP_PROFILES = {
    "352x288": ("352x288", "384k"),
    "320x240": ("320x240", "200k"),
    "176x144": ("176x144", "96k"),
}


def build_mp4_format_string(quality: str) -> str:
    """Fallback chain: prefer mp4 at the requested height -> any mp4 ->
    any video at that height -> best single stream. Keeps the same
    resilience the original hardcoded 1080p chain had (prevents
    'Requested format not available' on restricted/bot-detected videos),
    just parameterized by height now instead of fixed at 1080."""
    height = MP4_HEIGHTS.get(quality, None)
    if height is None:
        return (
            "bestvideo[ext=mp4]"
            "/bestvideo"
            "/best"
        )
    return (
        f"bestvideo[ext=mp4][height<={height}]"
        f"/bestvideo[ext=mp4]"
        f"/bestvideo[height<={height}]"
        f"/bestvideo"
        f"/best[height<={height}]"
        f"/best"
    )


def get_mp3_bitrate(quality: str) -> str:
    return MP3_BITRATES.get(quality, "192k")


def get_3gp_profile(quality: str):
    return THREEGP_PROFILES.get(quality, THREEGP_PROFILES["320x240"])


def ffmpeg_convert(input_files, output_file, ffmpeg_args=None, on_progress=None, on_speed=None, on_process_start=None):
    """Merge/convert with ffmpeg, reporting 0-100 progress via on_progress,
    and current encode speed (e.g. 2.5 meaning '2.5x realtime') via
    on_speed, parsed straight from ffmpeg's own 'speed=2.5x' stderr field.
    on_process_start(process), if given, is called right after the ffmpeg
    subprocess is spawned — lets a caller (e.g. the batch converter) keep
    a handle to terminate() it for pause/skip/delete on an in-flight job.
    Raises RuntimeError if ffmpeg isn't available."""
    if shutil.which("ffmpeg") is None and not os.path.exists(FFMPEG_PATH):
        raise RuntimeError(
            "ffmpeg not found. Run setup.bat, or install ffmpeg and add it to PATH."
        )
    ffmpeg_args = ffmpeg_args or []
    cmd = [FFMPEG_PATH, "-y"] + sum([["-i", f] for f in input_files], []) + ffmpeg_args + [output_file]

    process = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    if on_process_start:
        on_process_start(process)

    duration_pattern = re.compile(r"Duration: (\d+):(\d+):(\d+\.\d+)")
    time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
    speed_pattern = re.compile(r"speed=\s*([\d.]+)x")
    duration_seconds = None

    for line in process.stderr:
        line = line.strip()
        if duration_seconds is None:
            m = duration_pattern.search(line)
            if m:
                h, mi, s = m.groups()
                duration_seconds = int(h) * 3600 + int(mi) * 60 + float(s)
                continue
        m = time_pattern.search(line)
        if m and duration_seconds:
            h, mi, s = m.groups()
            current = int(h) * 3600 + int(mi) * 60 + float(s)
            percent = min(100, (current / duration_seconds) * 100)
            if on_progress:
                on_progress(percent)
        sm = speed_pattern.search(line)
        if sm and on_speed:
            try:
                on_speed(float(sm.group(1)))
            except ValueError:
                pass
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg exited with code {process.returncode}")


# ---------------------------------------------------------------
# YT-DLP DOWNLOAD (the only part that needs the cookie fallback)
# ---------------------------------------------------------------

def _make_progress_hook(on_chunk_percent, weight, base):
    """yt-dlp progress_hook -> overall job percent, plus raw download speed
    (bytes/sec) and ETA (seconds) straight from yt-dlp's own tracker.
    weight = how much of the total job (0-100) this download phase covers.
    base   = where this phase starts on the overall scale.
    on_chunk_percent(percent, speed_bytes_sec, eta_seconds) — caller decides
    how/whether to persist speed+eta (only one DB write per tick)."""
    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            pct = base + ((downloaded / total) * 100 / 100) * weight if total else base
            on_chunk_percent(pct, d.get("speed"), d.get("eta"))
    return hook


def _ytdlp_download(url, ydl_opts):
    from yt_dlp import YoutubeDL
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


def _download_streams(job, folder, on_progress, ydl_opts):
    """Downloads the raw stream(s) for one job. Returns dict of temp file
    paths keyed by stream type. Does NOT touch the database — caller does.
    This is the function wrapped by cookies.call_with_cookie_fallback, so
    it must accept ydl_opts as the cookie-injection point."""
    url = job["url"]
    fmt = job["format"].upper()
    # Tell yt-dlp exactly where ffmpeg/ffprobe are (portable folder support)
    _ffmpeg_dir = os.path.dirname(FFMPEG_PATH) if os.path.exists(FFMPEG_PATH) else None
    base_opts = {
        "quiet": True,
        "logger": SilentLogger(),
        "noplaylist": True,
        **( {"ffmpeg_location": _ffmpeg_dir} if _ffmpeg_dir else {} ),
    }

    if fmt == "MP3":
        audio_tmp = os.path.join(folder, f".tmp_{job['id']}_audio.webm")
        opts = {**base_opts, **ydl_opts, "outtmpl": audio_tmp, "format": "bestaudio/best",
                "progress_hooks": [_make_progress_hook(on_progress, weight=70, base=0)]}
        _ytdlp_download(url, opts)
        return {"audio": audio_tmp}

    elif fmt == "3GP":
        video_tmp = os.path.join(folder, f".tmp_{job['id']}_video.mp4")
        opts = {**base_opts, **ydl_opts, "outtmpl": video_tmp,
                "format": "18/best[height<=480][ext=mp4]/best[height<=480]/worst",
                "progress_hooks": [_make_progress_hook(on_progress, weight=70, base=0)]}
        _ytdlp_download(url, opts)
        return {"video": video_tmp}

    else:  # MP4
        video_tmp = os.path.join(folder, f".tmp_{job['id']}_video.mp4")
        audio_tmp = os.path.join(folder, f".tmp_{job['id']}_audio.m4a")
        video_fmt = build_mp4_format_string(job.get("quality", "best"))
        video_opts = {**base_opts, **ydl_opts, "outtmpl": video_tmp,
                      "format": video_fmt,
                      "progress_hooks": [_make_progress_hook(on_progress, weight=45, base=0)]}
        audio_opts = {**base_opts, **ydl_opts, "outtmpl": audio_tmp,
                      "format": "bestaudio[ext=m4a]/bestaudio/best",
                      "progress_hooks": [_make_progress_hook(on_progress, weight=25, base=45)]}
        _ytdlp_download(url, video_opts)
        _ytdlp_download(url, audio_opts)
        return {"video": video_tmp, "audio": audio_tmp}


# ---------------------------------------------------------------
# ONE JOB, START TO FINISH
# ---------------------------------------------------------------

def process_job(job_id: str, worker_name: str = "worker"):
    """STEP 4-7 of the workflow: download, convert, save, mark completed.
    Any failure marks the job 'failed' with the error recorded — the job
    stays in the database for the user to retry."""
    job = db.get_job(job_id)
    if job is None:
        return

    folder = ensure_group_folder(job)
    fmt = job["format"].upper()

    def on_progress(percent, speed_bytes_sec=None, eta_seconds=None):
        q.set_progress(job_id, round(min(percent, 99), 1),
                        speed_bytes_sec=speed_bytes_sec, eta_seconds=eta_seconds)

    def on_conv_speed(speed_x):
        q.set_conversion_speed(job_id, speed_x)

    def on_conv_progress(percent):
        q.set_convert_percent(job_id, round(percent, 1))

    temp_files = []
    try:
        streams, used_cookies = ck.call_with_cookie_fallback(
            lambda **kw: _download_streams(job, folder, on_progress, ydl_opts=kw.get("ydl_opts", {})),
            ydl_opts={},
        )
        if used_cookies:
            q.mark_used_cookies(job_id)

        q.mark_converting(job_id)

        if fmt == "MP3":
            temp_files = [streams["audio"]]
            output_path = os.path.join(folder, build_filename(job, ".mp3"))
            bitrate = get_mp3_bitrate(job.get("quality", "192k"))
            ffmpeg_convert(
                temp_files, output_path,
                ["-c:a", "libmp3lame", "-b:a", bitrate],
                on_progress=on_conv_progress,
                on_speed=on_conv_speed,
            )

        elif fmt == "3GP":
            temp_files = [streams["video"]]
            output_path = os.path.join(folder, build_filename(job, ".3gp"))
            resolution, video_bitrate = get_3gp_profile(job.get("quality", "320x240"))
            ffmpeg_convert(
                temp_files, output_path,
                ["-s", resolution, "-c:v", "mpeg4", "-b:v", video_bitrate, "-c:a", "aac", "-ac", "1"],
                on_progress=on_conv_progress,
                on_speed=on_conv_speed,
            )

        else:  # MP4
            temp_files = [streams["video"], streams["audio"]]
            output_path = os.path.join(folder, build_filename(job, ".mp4"))
            ffmpeg_convert(
                temp_files, output_path,
                ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"],
                on_progress=on_conv_progress,
                on_speed=on_conv_speed,
            )

        for f in temp_files:
            if os.path.exists(f):
                os.remove(f)

        q.mark_completed(job_id, output_path)

    except Exception as e:
        retryable = _is_retryable_error(e)
        err_text = clean_error_text(e)
        delay = q.schedule_retry(job_id, err_text) if retryable else None

        if delay is not None:
            # Transient error, auto-retry budget not exhausted — leave
            # temp_files ON DISK. yt-dlp defaults to continuedl=True, so
            # the next attempt (same job_id -> same temp filename) picks
            # up from the existing .part file instead of starting the
            # download over from zero.
            log.warning(f"job {job_id} failed (retryable), auto-retry in {delay}s: {e}")
        else:
            # Either a permanent error, or a transient one that's used up
            # its auto-retry attempts — no point keeping partial files
            # around indefinitely, and a manual retry starts fresh anyway.
            _cleanup_temp_files(temp_files)
            log.exception(f"job {job_id} failed ({job.get('url')})")
            q.mark_failed(job_id, err_text)


# ---------------------------------------------------------------
# WORKER POOL
# ---------------------------------------------------------------

# ---------------------------------------------------------------
# RETRY CLASSIFICATION — which failures are worth an automatic retry
# ---------------------------------------------------------------

# Substrings matched against str(exception), case-insensitive. These are
# transient/network-ish conditions where trying again shortly (with
# backoff) has a real chance of succeeding.
_RETRYABLE_PATTERNS = (
    "timed out", "timeout", "connection reset", "connection aborted",
    "connection refused", "temporary failure", "urlopen error",
    "network is unreachable", "http error 429", "http error 500",
    "http error 502", "http error 503", "http error 504",
    "unable to download webpage", "remote end closed connection",
    "read timed out", "ssl", "econnreset",
)

# Substrings for conditions where retrying is pointless — the video
# genuinely isn't downloadable, no amount of waiting fixes it.
_PERMANENT_PATTERNS = (
    "private video", "video unavailable", "this video is not available",
    "video has been removed", "account associated with this video",
    "copyright", "sign in to confirm your age", "age-restricted",
    "this live event", "members-only", "no video formats found",
)


def _is_retryable_error(exc) -> bool:
    msg = str(exc).lower()
    if any(p in msg for p in _PERMANENT_PATTERNS):
        return False
    return any(p in msg for p in _RETRYABLE_PATTERNS)


def _cleanup_temp_files(temp_files):
    for f in temp_files:
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass


# ---------------------------------------------------------------
# AUTO-RETRY SWEEP — separate from the WorkerPool: just watches for
# failed jobs whose backoff timer has elapsed and requeues them. The
# WorkerPool's own claim loop picks them up normally from there.
# ---------------------------------------------------------------

def _retry_sweep_loop():
    while True:
        try:
            for job in db.get_jobs_due_for_retry():
                try:
                    q.retry_job(job["id"])
                    log.info(f"auto-retrying job {job['id']} (attempt {job['retry_count'] + 1})")
                except Exception:
                    log.exception(f"could not auto-retry job {job['id']}")
        except Exception:
            log.exception("retry sweep loop error")
        time.sleep(5)


def start_retry_sweeper():
    threading.Thread(target=_retry_sweep_loop, daemon=True).start()


class WorkerPool:
    """Spawns N daemon threads, each in a loop: claim a queued job, run
    it to completion, repeat. Worker count comes from Settings.

    Supports live resize: growing spawns new threads immediately;
    shrinking asks the highest-numbered threads to exit after they
    finish whatever job they're currently on (never kills mid-download).
    """

    def __init__(self, poll_interval=1.5):
        self.poll_interval = poll_interval
        self._workers = {}          # name -> {"thread": Thread, "stop": Event}
        self._pool_lock = threading.Lock()
        self._stop_event = threading.Event()  # global shutdown (app exit)
        self._next_index = 1

    def _worker_loop(self, name, stop_event):
        while not self._stop_event.is_set() and not stop_event.is_set():
            job = q.claim_job(name)
            if job is None:
                stop_event.wait(self.poll_interval)
                continue
            try:
                process_job(job["id"], worker_name=name)
            except Exception as e:
                log.exception(f"worker {name} crashed on job {job['id']}")
                q.mark_failed(job["id"], clean_error_text(f"worker crashed: {e}"))
        with self._pool_lock:
            self._workers.pop(name, None)

    def _spawn_one(self):
        name = f"worker-{self._next_index}"
        self._next_index += 1
        stop_event = threading.Event()
        t = threading.Thread(target=self._worker_loop, args=(name, stop_event), daemon=True)
        self._workers[name] = {"thread": t, "stop": stop_event}
        t.start()

    def start(self, num_workers=None):
        q.reset_stale_locks()
        num_workers = num_workers or q.get_concurrent_limit()
        with self._pool_lock:
            for _ in range(num_workers):
                self._spawn_one()
            return len(self._workers)

    def resize(self, new_count):
        """Adjust live worker count without restarting the app.
        Growing: spawns new threads right away.
        Shrinking: signals the extra threads to stop after their
        current job (they self-remove from _workers when done, so
        this never interrupts an in-progress download)."""
        new_count = max(1, min(int(new_count), 10))
        with self._pool_lock:
            current = len(self._workers)
            if new_count > current:
                for _ in range(new_count - current):
                    self._spawn_one()
            elif new_count < current:
                # Stop the most-recently-spawned workers first
                names = sorted(self._workers.keys(),
                                key=lambda n: int(n.split("-")[-1]), reverse=True)
                for name in names[:current - new_count]:
                    self._workers[name]["stop"].set()
            return new_count

    def active_count(self):
        with self._pool_lock:
            return len(self._workers)

    def stop(self):
        self._stop_event.set()
