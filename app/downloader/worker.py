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
import database as db   # noqa: E402
import cookies as ck    # noqa: E402
import job_queue as q        # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(_THIS_DIR))


def _windows_downloads_folder():
    """Resolve the real Windows 'Downloads' folder for the current user,
    respecting the case where they've relocated it (e.g. to another
    drive) via the Known Folder API — falls back to the standard
    ~/Downloads path if that lookup isn't available (non-Windows, or
    the API call fails for any reason)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            FOLDERID_Downloads = "{374DE290-123F-4565-9164-39C4925E467B}"
            guid = ctypes.create_unicode_buffer(FOLDERID_Downloads)
            buf = ctypes.c_wchar_p()
            # SHGetKnownFolderPath wants a GUID struct, not a string — build one
            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8)]

            rfid = GUID()
            ctypes.windll.ole32.CLSIDFromString(guid, ctypes.byref(rfid))
            ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(rfid), 0, 0, ctypes.byref(buf)
            )
            if buf.value:
                return buf.value
        except Exception:
            pass  # fall through to the simple default below

    return os.path.join(os.path.expanduser("~"), "Downloads")


_DEFAULT_DOWNLOADS = os.path.join(_windows_downloads_folder(), "YT Downloader")


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


def ffmpeg_convert(input_files, output_file, ffmpeg_args=None, on_progress=None):
    """Merge/convert with ffmpeg, reporting 0-100 progress via on_progress.
    Raises RuntimeError if ffmpeg isn't available."""
    if shutil.which("ffmpeg") is None and not os.path.exists(FFMPEG_PATH):
        raise RuntimeError(
            "ffmpeg not found. Run setup.bat, or install ffmpeg and add it to PATH."
        )
    ffmpeg_args = ffmpeg_args or []
    cmd = [FFMPEG_PATH, "-y"] + sum([["-i", f] for f in input_files], []) + ffmpeg_args + [output_file]

    process = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")

    duration_pattern = re.compile(r"Duration: (\d+):(\d+):(\d+\.\d+)")
    time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
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
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"ffmpeg exited with code {process.returncode}")


# ---------------------------------------------------------------
# YT-DLP DOWNLOAD (the only part that needs the cookie fallback)
# ---------------------------------------------------------------

def _make_progress_hook(on_chunk_percent, weight, base):
    """yt-dlp progress_hook -> overall job percent.
    weight = how much of the total job (0-100) this download phase covers.
    base   = where this phase starts on the overall scale."""
    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            if total:
                phase_pct = (downloaded / total) * 100
                on_chunk_percent(base + (phase_pct / 100) * weight)
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

    def on_progress(percent):
        q.set_progress(job_id, round(min(percent, 99), 1))

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
                on_progress=lambda p: on_progress(99),
            )

        elif fmt == "3GP":
            temp_files = [streams["video"]]
            output_path = os.path.join(folder, build_filename(job, ".3gp"))
            resolution, video_bitrate = get_3gp_profile(job.get("quality", "320x240"))
            ffmpeg_convert(
                temp_files, output_path,
                ["-s", resolution, "-c:v", "mpeg4", "-b:v", video_bitrate, "-c:a", "aac", "-ac", "1"],
                on_progress=lambda p: on_progress(99),
            )

        else:  # MP4
            temp_files = [streams["video"], streams["audio"]]
            output_path = os.path.join(folder, build_filename(job, ".mp4"))
            ffmpeg_convert(
                temp_files, output_path,
                ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"],
                on_progress=lambda p: on_progress(99),
            )

        for f in temp_files:
            if os.path.exists(f):
                os.remove(f)

        q.mark_completed(job_id, output_path)

    except Exception as e:
        for f in temp_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
        q.mark_failed(job_id, str(e))


# ---------------------------------------------------------------
# WORKER POOL
# ---------------------------------------------------------------

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
                q.mark_failed(job["id"], f"worker crashed: {e}")
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
