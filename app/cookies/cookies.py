# app/cookies/cookies.py
"""
Cookie System — the most important part to get right.

Rule 1: Never use cookies by default. Without cookies, yt-dlp is fastest.
Rule 2: Cookies only when required. Try without cookies first; only retry
        with cookies if the failure looks like a login/age/region/bot wall.

Cookie Priority:
    1. No Cookies
    2. Browser Cookies (synced cookies.txt)
    3. Fail

Never reverse this order. This module is shared by Scanner (metadata
probing) and Downloader (actual download), so the policy is enforced
in exactly one place.
"""

import os
import time
import re

import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database"))
import database as db  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COOKIES_DIR = os.path.join(BASE_DIR, "cookies")
COOKIES_PATH = os.environ.get("YTDLP_COOKIES_PATH") or os.path.join(COOKIES_DIR, "cookies.txt")

# Substrings of yt-dlp error messages that indicate "this needs a login,
# retry with cookies" — anything else is just a normal failure and should
# NOT trigger a cookie retry (e.g. video deleted, network blip, bad URL).
_COOKIE_RETRY_SIGNALS = (
    "sign in to confirm",
    "login required",
    "age restricted",
    "age-restricted",
    "private video",
    "members only",
    "members-only",
    "premium",
    "this video is unavailable",  # sometimes age/region gated, worth one retry
    "confirm you're not a bot",
    "http error 403",
    "403:",
    "http error 429",
    "429:",
)


def cookies_available():
    return os.path.exists(COOKIES_PATH) and os.path.getsize(COOKIES_PATH) > 0


def with_cookies(ydl_opts: dict):
    """Attach the cookie file to yt-dlp options. Only call this on retry —
    never on the first attempt (Rule 1)."""
    opts = dict(ydl_opts)
    if cookies_available():
        opts["cookiefile"] = COOKIES_PATH
    return opts


def needs_cookie_retry(error: Exception) -> bool:
    """Decide whether a failed yt-dlp call is worth retrying with cookies.
    Only known login/age/bot-detection style failures qualify."""
    message = str(error).lower()
    return any(signal in message for signal in _COOKIE_RETRY_SIGNALS)


def sync_cookies_from_browser(cookie_list):
    """Write cookies (as received from the browser extension) into a
    Netscape-format cookies.txt for yt-dlp, and record sync status in DB."""
    if not cookie_list:
        raise ValueError("no cookies received")

    os.makedirs(COOKIES_DIR, exist_ok=True)
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookie_list:
        domain = c.get("domain", "")
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        path_ = c.get("path", "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expiry = str(int(c.get("expirationDate", time.time() + 3600 * 24 * 365)))
        name = c.get("name", "")
        value = c.get("value", "")
        lines.append("\t".join([domain, include_sub, path_, secure, expiry, name, value]))

    with open(COOKIES_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    db.set_cookies_status(True, cookie_count=len(cookie_list))
    return len(cookie_list)


def delete_cookies():
    if os.path.exists(COOKIES_PATH):
        os.remove(COOKIES_PATH)
    db.set_cookies_status(False, cookie_count=0)


def status():
    """DB is the source of truth for whether cookies are synced, but we
    cross-check the file still exists on disk in case it was deleted
    outside the app."""
    db_status = db.get_cookies_status()
    if db_status["synced"] and not cookies_available():
        db.set_cookies_status(False, cookie_count=0)
        db_status = db.get_cookies_status()
    return db_status


def call_with_cookie_fallback(fn, *args, **kwargs):
    """Run fn(*args, **kwargs, ydl_opts=opts) first WITHOUT cookies.
    If it fails with a login/age/bot-detection style error AND cookies
    are available, retry once WITH cookies. Returns (result, used_cookies).

    `fn` must accept a keyword argument `ydl_opts` and raise on failure.
    """
    base_opts = kwargs.pop("ydl_opts", {})

    try:
        result = fn(*args, ydl_opts=dict(base_opts), **kwargs)
        return result, False
    except Exception as e:
        if cookies_available() and needs_cookie_retry(e):
            result = fn(*args, ydl_opts=with_cookies(base_opts), **kwargs)
            return result, True
        raise
