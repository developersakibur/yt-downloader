# app/scanner/scanner.py
"""
Scanner — Rule 1: Scanner NEVER downloads.

Scanner Responsibilities (and nothing else):
    Validate URL
    Extract metadata
    Download thumbnail
    Store metadata
    Create folder entry (a Group row — actual mkdir happens at download time)

Every video the scanner finds is inserted into video_jobs as one
independent job with status='queued' (Rule 3).
"""

import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookies"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloader"))
import database as db   # noqa: E402
import cookies as ck    # noqa: E402
import worker as _worker  # noqa: E402  — reused for get_downloads_root() + set_hidden()

_thumb_dir_ready = False  # ensure we only touch the hidden attribute once per run


def get_thumbnail_dir() -> str:
    """Thumbnails live inside <downloads_root>/.thumbnails/ — a hidden
    folder nested in the same wrapper as the actual downloads, so moving
    or backing up the wrapper folder keeps everything together without
    cluttering Explorer with a visible cache folder."""
    global _thumb_dir_ready
    path = os.path.join(_worker.get_downloads_root(), ".thumbnails")
    if not _thumb_dir_ready:
        os.makedirs(path, exist_ok=True)
        _worker.set_hidden(path)
        _thumb_dir_ready = True
    return path


class SilentLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


# ---------------------------------------------------------------
# URL VALIDATION + TYPE DETECTION
# ---------------------------------------------------------------

VALID_TYPES = (
    "single", "short", "playlist", "search",
    "channel-longs", "channel-shorts", "channel-full",
)


def is_valid_youtube_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    return bool(re.search(r"(youtube\.com|youtu\.be)", url.strip(), re.IGNORECASE))


def detect_type(url: str, force_playlist: bool = False) -> str:
    """Classify a raw URL into one of VALID_TYPES, or 'unknown'."""
    url = url.strip()

    if "youtube.com/results" in url:
        return "search"
    if (("watch?v=" in url) or ("youtu.be/" in url)) and "list=" in url:
        return "playlist" if force_playlist else "single"
    if "watch?v=" in url or "youtu.be/" in url:
        return "single"
    if "/shorts/" in url:
        return "short"
    if "playlist" in url and "list=" in url:
        return "playlist"
    if re.search(r"youtube\.com/@[^/]+/shorts/?$", url):
        return "channel-shorts"
    if re.search(r"youtube\.com/@[^/]+/videos/?$", url):
        return "channel-longs"
    if re.match(r"https?://(www\.)?youtube\.com/@[^/]+/?$", url):
        return "channel-full"
    return "unknown"


def build_extract_url(url: str, url_type: str, quantity: int = 25) -> str:
    """Translate the raw browser URL into what yt-dlp should fetch."""
    if url_type == "search":
        m = re.search(r"search_query=([^&]+)", url)
        query = (m.group(1).replace("+", " ") if m else "").strip()
        try:
            qty = int(quantity)
        except (TypeError, ValueError):
            qty = 25
        qty = max(1, min(qty, 100))
        return f"ytsearch{qty}:{query}"
    return url


def _group_name_for(url_type: str) -> str:
    return {
        "playlist": "Playlist",
        "search": "Search",
        "channel-longs": "Channel",
        "channel-shorts": "Channel Shorts",
        "channel-full": "Channel",
    }.get(url_type, "Group")


# ---------------------------------------------------------------
# THUMBNAIL CACHING
# ---------------------------------------------------------------

def cached_thumbnail_path(video_id: str) -> str:
    return os.path.join(get_thumbnail_dir(), f"{video_id}.jpg")


def download_thumbnail(video_id: str, thumbnail_url: str) -> str | None:
    """Save the thumbnail once to local cache. UI must always read from
    here afterwards, never re-request YouTube directly."""
    if not video_id or not thumbnail_url:
        return None

    dest = cached_thumbnail_path(video_id)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest  # already cached

    os.makedirs(get_thumbnail_dir(), exist_ok=True)
    try:
        import requests
        resp = requests.get(thumbnail_url, timeout=10)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            f.write(resp.content)
        return dest
    except Exception:
        return None  # non-fatal — job still gets created without a thumbnail


# ---------------------------------------------------------------
# METADATA EXTRACTION (yt-dlp probing only — download=False, always)
# ---------------------------------------------------------------

def _extract_flat(extract_url: str, ydl_opts: dict, quantity: int = 100):
    """List entries of a playlist/search/channel WITHOUT fetching full
    per-video metadata (fast). Used to enumerate what to scan.
    playlist_items limits yt-dlp to exactly the requested count so a
    search for 25 never accidentally pulls 1000+ items."""
    from yt_dlp import YoutubeDL
    opts = {
        "quiet": True,
        "logger": SilentLogger(),
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlist_items": f"1-{quantity}",   # hard cap — fixes search overrun
        **ydl_opts,
    }
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(extract_url, download=False)


def _extract_full(url: str, ydl_opts: dict):
    """Full metadata for exactly one video."""
    from yt_dlp import YoutubeDL
    opts = {
        "quiet": True,
        "logger": SilentLogger(),
        "noplaylist": True,
        "skip_download": True,
        **ydl_opts,
    }
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def probe(extract_url: str, flat: bool, quantity: int = 100):
    """No-cookies-first probing, retried once with cookies on a login/age/
    bot-detection style failure (Cookie Priority rule)."""
    if flat:
        fn = lambda ydl_opts: _extract_flat(extract_url, ydl_opts, quantity=quantity)
        info, used_cookies = ck.call_with_cookie_fallback(fn, ydl_opts={})
    else:
        info, used_cookies = ck.call_with_cookie_fallback(_extract_full, extract_url, ydl_opts={})
    return info, used_cookies


# ---------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------

def scan(url: str, quantity: int = 25, force_playlist: bool = False,
         format: str = "MP4", quality: str = "best") -> dict:
    """Validate -> extract metadata -> cache thumbnails -> store in DB.
    Returns a summary dict: {group_id, job_ids, type, used_cookies}.
    Raises ValueError for invalid/unrecognized URLs."""

    if not is_valid_youtube_url(url):
        raise ValueError("Not a valid YouTube URL")

    url_type = detect_type(url, force_playlist=force_playlist)
    if url_type == "unknown":
        raise ValueError(f"Could not recognize URL type: {url}")

    extract_url = build_extract_url(url, url_type, quantity)
    is_group_type = url_type in ("playlist", "search", "channel-longs", "channel-shorts", "channel-full")

    info, used_cookies = probe(extract_url, flat=is_group_type, quantity=int(quantity))

    job_ids = []
    group_id = None

    if is_group_type:
        entries = [e for e in (info.get("entries") or []) if e]  # drop unavailable (None) entries
        group_name = info.get("title") or info.get("uploader") or info.get("channel") or _group_name_for(url_type)
        group_id = db.create_group(
            type_=url_type,
            name=group_name,
            source_url=url,
            total_videos=len(entries),
        )

        for idx, entry in enumerate(entries, start=1):
            video_id = entry.get("id")
            video_url = entry.get("url") or (f"https://youtu.be/{video_id}" if video_id else None)
            if not video_url:
                continue
            thumb_url = entry.get("thumbnail") or _best_thumbnail(entry.get("thumbnails"))
            thumb_path = download_thumbnail(video_id, thumb_url) if video_id and thumb_url else None

            job_id = db.create_job(
                url=video_url,
                group_id=group_id,
                video_id=video_id,
                original_title=entry.get("title"),
                thumbnail_path=thumb_path,
                duration=entry.get("duration"),
                uploader=entry.get("uploader") or entry.get("channel"),
                playlist_index=idx,
                format=format.upper(),
                quality=quality,
            )
            job_ids.append(job_id)

    else:
        video_id = info.get("id")
        thumb_url = info.get("thumbnail") or _best_thumbnail(info.get("thumbnails"))
        thumb_path = download_thumbnail(video_id, thumb_url) if video_id and thumb_url else None

        job_id = db.create_job(
            url=url,
            group_id=None,
            video_id=video_id,
            original_title=info.get("title"),
            thumbnail_path=thumb_path,
            duration=info.get("duration"),
            uploader=info.get("uploader") or info.get("channel"),
            playlist_index=None,
            format=format.upper(),
            quality=quality,
        )
        job_ids.append(job_id)

    return {
        "type": url_type,
        "group_id": group_id,
        "job_ids": job_ids,
        "video_count": len(job_ids),
        "used_cookies": used_cookies,
    }


def _best_thumbnail(thumbnails):
    if not thumbnails:
        return None
    try:
        return sorted(thumbnails, key=lambda t: t.get("width", 0) or 0)[-1].get("url")
    except Exception:
        return thumbnails[-1].get("url") if isinstance(thumbnails, list) else None
