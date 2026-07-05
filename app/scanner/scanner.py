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


_ITEM_PROGRESS_RE = re.compile(r"Downloading item (\d+) of (\d+)")


class _ProgressLogger:
    """Same as SilentLogger, but also parses yt-dlp's own
    '[download] Downloading item N of M' lines (which it prints while
    paginating through a playlist/channel listing) and forwards them to
    on_progress(fetched, total) — lets the UI show a live '49/162'
    counter while a big playlist scan is still running, instead of
    looking frozen until the whole thing finishes."""
    def __init__(self, on_progress=None):
        self.on_progress = on_progress

    def debug(self, msg):
        if not self.on_progress:
            return
        m = _ITEM_PROGRESS_RE.search(msg)
        if m:
            self.on_progress(int(m.group(1)), int(m.group(2)))

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

def _extract_flat(extract_url: str, ydl_opts: dict, quantity: int | None = 100, on_progress=None):
    """List entries of a playlist/search/channel WITHOUT fetching full
    per-video metadata (fast). Single yt-dlp call — only safe for small
    requests (<=~100), see _extract_flat_chunked for anything larger.
    quantity=None means no limit — omit playlist_items entirely."""
    from yt_dlp import YoutubeDL
    opts = {
        "quiet": True,
        "logger": _ProgressLogger(on_progress) if on_progress else SilentLogger(),
        "extract_flat": "in_playlist",
        "skip_download": True,
        **ydl_opts,
    }
    if quantity is not None:
        opts["playlist_items"] = f"1-{quantity}"
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(extract_url, download=False)


_CHUNK_SIZE = 100


def _extract_flat_chunked(extract_url: str, ydl_opts: dict, max_total: int, on_progress=None):
    """Works around a known, still-open yt-dlp/YouTube bug where a single
    flat-playlist extraction call silently stops around 100-200 items on
    longer playlists/channels — YouTube's continuation token stops
    resolving past that point (yt-dlp issues #12759, #16943, #14522).
    Neither an error nor a short result is raised; it just quietly hands
    back fewer entries than actually exist.

    Workaround: split into fixed-size chunks (playlist_items "1-100",
    "101-200", ...), each a fresh yt-dlp call that starts its own
    continuation from page 1 — so no single call ever crosses the point
    where YouTube's pagination breaks. Stitches the chunks into one
    combined result.

    max_total caps the absolute number fetched (safety net for search's
    250 cap and playlist/channel's 999 cap on 'All')."""
    from yt_dlp import YoutubeDL
    all_entries = []
    playlist_count = None
    group_title = None
    start = 1
    while start <= max_total:
        end = min(start + _CHUNK_SIZE - 1, max_total)
        opts = {
            "quiet": True,
            "logger": SilentLogger(),
            "extract_flat": "in_playlist",
            "skip_download": True,
            "playlist_items": f"{start}-{end}",
            **ydl_opts,
        }
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(extract_url, download=False)

        if playlist_count is None:
            playlist_count = info.get("playlist_count")
            group_title = info.get("title") or info.get("uploader") or info.get("channel")

        chunk_entries = [e for e in (info.get("entries") or []) if e]
        if not chunk_entries:
            break  # nothing left — reached the real end of the list

        all_entries.extend(chunk_entries)
        if on_progress:
            on_progress(len(all_entries), playlist_count or max_total)

        requested_size = end - start + 1
        if len(chunk_entries) < requested_size:
            break  # short chunk == end of the actual list, stop here
        if playlist_count and len(all_entries) >= playlist_count:
            break

        start = end + 1

    return {"entries": all_entries, "title": group_title, "playlist_count": playlist_count}


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


def probe(extract_url: str, flat: bool, quantity: int | None = 100, on_progress=None, prefer_cookies=False):
    """No-cookies-first probing, retried once with cookies on a login/age/
    bot-detection style failure (Cookie Priority rule) — UNLESS
    prefer_cookies=True, which sends cookies from the first attempt.
    Bulk playlist/channel 'All' scans use prefer_cookies=True because
    YouTube silently truncates long anonymous listings without raising
    any error for the normal retry path to catch.
    quantity=None (flat mode only) is treated as 'no explicit cap' —
    max_total falls back to a safety ceiling (see scan_preview)."""
    if flat:
        max_total = quantity if quantity is not None else 999
        fn = lambda ydl_opts: _extract_flat_chunked(extract_url, ydl_opts, max_total, on_progress=on_progress)
        info, used_cookies = ck.call_with_cookie_fallback(fn, ydl_opts={}, prefer_cookies=prefer_cookies)
    else:
        info, used_cookies = ck.call_with_cookie_fallback(_extract_full, extract_url, ydl_opts={})
    return info, used_cookies


def quick_count(url: str, force_playlist: bool = False) -> dict:
    """Fast, minimal probe just to find out how many videos a
    playlist/channel has — used for the 'Whole playlist' scope option on
    a video+list URL (and the extension's equivalent) so the person sees
    a real count before committing to a full scan. Fetches just 1 item
    (playlist_items=1-1) but yt-dlp still reports the true total via
    playlist_count even when only asked for one."""
    if not is_valid_youtube_url(url):
        raise ValueError("Not a valid YouTube URL")

    url_type = detect_type(url, force_playlist=force_playlist)
    if url_type == "unknown":
        raise ValueError(f"Could not recognize URL type: {url}")

    extract_url = build_extract_url(url, url_type, 1)
    info, _ = probe(extract_url, flat=True, quantity=1)
    return {
        "type": url_type,
        "count": info.get("playlist_count"),
        "title": info.get("title") or info.get("uploader") or info.get("channel"),
    }


def scan_preview(url: str, quantity: int | str = 25, force_playlist: bool = False,
                  on_progress=None, on_entries=None) -> dict:
    """Like scan(), but for group-type URLs (playlist/search/channel) only
    metadata + thumbnails are fetched — nothing is written to video_jobs
    or groups. Used to populate the batch-selection popup so the person
    can choose which videos to actually queue.

    on_progress(fetched, total), if given, is called live during both the
    listing phase (yt-dlp paginating the playlist/channel) and the
    thumbnail-fetch phase — lets the UI show a running '49/162' counter
    instead of looking frozen until the whole scan finishes.

    on_entries(list_of_one_entry_dict), if given, is called immediately
    after each individual video's metadata + thumbnail are ready — lets
    the caller (routes.py) push it into the DB right away, so the
    Approve tab can show videos appearing one by one instead of waiting
    for the entire scan to finish (partial approve is allowed).

    Single video / short should never call this — the caller (routes.py)
    keeps using scan() directly for those, unchanged, no popup involved.
    Raises ValueError for invalid/unrecognized/non-group URLs."""

    if not is_valid_youtube_url(url):
        raise ValueError("Not a valid YouTube URL")

    url_type = detect_type(url, force_playlist=force_playlist)
    if url_type == "unknown":
        raise ValueError(f"Could not recognize URL type: {url}")
    if url_type not in ("playlist", "search", "channel-longs", "channel-shorts", "channel-full"):
        raise ValueError("scan_preview is only for playlist/search/channel URLs")

    # Caps: search results are an endless feed, so "All" still means
    # "up to 250" rather than truly unlimited. Playlists/channels are
    # finite, so "All" means "as many as actually exist, up to a 999
    # safety ceiling" (nobody's queuing 1000 videos from one playlist by
    # accident).
    SEARCH_MAX = 250
    GROUP_MAX = 999

    quantity_for_extract = None
    if url_type == "search":
        try:
            q = int(quantity)
        except (TypeError, ValueError):
            q = 25
        quantity_for_extract = max(1, min(q, SEARCH_MAX))
    else:
        if isinstance(quantity, str) and quantity.strip().lower() == "all":
            quantity_for_extract = GROUP_MAX
        else:
            try:
                q = int(quantity)
            except (TypeError, ValueError):
                q = 25
            quantity_for_extract = max(1, min(q, GROUP_MAX))

    # Bulk playlist/channel listings ("All", or any largeish request) get
    # silently truncated by YouTube for anonymous requests — no error is
    # raised, so the normal retry-on-failure cookie logic never kicks in.
    # Sending cookies upfront (when the user has them synced) avoids that
    # truncation. Search doesn't have this problem the same way (always
    # capped, and chunked the same as everything else), so it keeps the
    # no-cookies-first default.
    prefer_cookies = url_type != "search"

    extract_url = build_extract_url(url, url_type, quantity_for_extract)
    info, used_cookies = probe(extract_url, flat=True, quantity=quantity_for_extract,
                                on_progress=on_progress, prefer_cookies=prefer_cookies)

    entries = [e for e in (info.get("entries") or []) if e]
    group_name = info.get("title") or info.get("uploader") or info.get("channel") or _group_name_for(url_type)

    preview_entries = []
    for idx, entry in enumerate(entries, start=1):
        video_id = entry.get("id")
        video_url = entry.get("url") or (f"https://youtu.be/{video_id}" if video_id else None)
        if not video_url:
            continue
        thumb_url = entry.get("thumbnail") or _best_thumbnail(entry.get("thumbnails"))
        thumb_path = download_thumbnail(video_id, thumb_url) if video_id and thumb_url else None

        preview_entry = {
            "video_id": video_id,
            "url": video_url,
            "title": entry.get("title"),
            "thumbnail_path": thumb_path,
            "duration": entry.get("duration"),
            "uploader": entry.get("uploader") or entry.get("channel"),
            "playlist_index": idx,
        }
        preview_entries.append(preview_entry)
        if on_entries:
            on_entries([preview_entry])
        if on_progress:
            on_progress(idx, len(entries))  # thumbnail-fetch phase, same counter
        if on_progress:
            on_progress(idx, len(entries))  # thumbnail-fetch phase, same counter

    return {
        "type": url_type,
        "group_name": group_name,
        "source_url": url,
        "entries": preview_entries,
        "used_cookies": used_cookies,
    }


def confirm_batch(source_url: str, url_type: str, group_name: str,
                   entries: list, format: str = "MP4", quality: str = "best") -> dict:
    """Creates the Group + one Job per selected entry. Called only after
    the person confirms their selection in the popup — this is the sole
    place group-type jobs get written to the DB (scan_preview never
    writes anything).

    `entries` — list of dicts as returned by scan_preview's "entries",
    each optionally carrying a "prefix" (int) set by the frontend when
    the "add number prefix" toggle is on; omitted/None means no prefix
    for that job."""

    if url_type not in ("playlist", "search", "channel-longs", "channel-shorts", "channel-full"):
        raise ValueError("confirm_batch is only for playlist/search/channel URLs")
    if not entries:
        raise ValueError("No videos selected")

    format = (format or "MP4").upper()

    group_id = db.create_group(
        type_=url_type,
        name=group_name or _group_name_for(url_type),
        source_url=source_url,
        total_videos=len(entries),
    )

    job_ids = []
    for entry in entries:
        video_url = entry.get("url")
        if not video_url:
            continue
        job_id = db.create_job(
            url=video_url,
            group_id=group_id,
            video_id=entry.get("video_id"),
            original_title=entry.get("title"),
            thumbnail_path=entry.get("thumbnail_path"),
            duration=entry.get("duration"),
            uploader=entry.get("uploader"),
            playlist_index=entry.get("playlist_index"),
            selection_prefix=entry.get("prefix"),
            format=format,
            quality=quality,
        )
        job_ids.append(job_id)

    return {"group_id": group_id, "job_ids": job_ids, "video_count": len(job_ids)}


# ---------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------

def scan(url: str, quantity: int | str = 25, force_playlist: bool = False,
         format: str = "MP4", quality: str = "best") -> dict:
    """Validate -> extract metadata -> cache thumbnails -> store in DB.
    quantity: an int, or the string 'all' (playlist/channel only — search
    is always capped at 100 regardless of what's passed here).
    Returns a summary dict: {group_id, job_ids, type, used_cookies}.
    Raises ValueError for invalid/unrecognized URLs."""

    if not is_valid_youtube_url(url):
        raise ValueError("Not a valid YouTube URL")

    url_type = detect_type(url, force_playlist=force_playlist)
    if url_type == "unknown":
        raise ValueError(f"Could not recognize URL type: {url}")

    is_group_type = url_type in ("playlist", "search", "channel-longs", "channel-shorts", "channel-full")

    # Quantity normalization — server-side, independent of what the client
    # sent, so a stale/tampered request can never bypass these rules:
    #   - search: capped at 250 ('All' doesn't apply — YouTube search is
    #     an endless feed, there's no natural 'all')
    #   - playlist / channel: quantity='all' means "everything, up to a
    #     999 safety ceiling" (see scan_preview for why not truly None)
    #   - single / short: quantity is irrelevant, ignored
    SEARCH_MAX = 250
    GROUP_MAX = 999
    quantity_for_extract = None
    if url_type == "search":
        try:
            q = int(quantity)
        except (TypeError, ValueError):
            q = 25
        quantity_for_extract = max(1, min(q, SEARCH_MAX))
    elif is_group_type:
        if isinstance(quantity, str) and quantity.strip().lower() == "all":
            quantity_for_extract = GROUP_MAX
        else:
            try:
                q = int(quantity)
            except (TypeError, ValueError):
                q = 25
            quantity_for_extract = max(1, min(q, GROUP_MAX))

    extract_url = build_extract_url(url, url_type, quantity_for_extract or 25)

    info, used_cookies = probe(extract_url, flat=is_group_type, quantity=quantity_for_extract,
                                prefer_cookies=is_group_type and url_type != "search")

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
