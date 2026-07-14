# app/errors.py
"""
clean_error_text() — turns a raw exception string (yt-dlp/ffmpeg output
often contains ANSI color codes like '\x1b[0;31mERROR:\x1b[0m', or the
literal bracket codes left behind when the escape byte gets stripped
by a non-tty pipe) into a single-line, tooltip-friendly message.

Used everywhere an error gets saved to the DB (job_queue.mark_failed,
job_queue.schedule_retry, converter.py) so the stored text is already
clean — the frontend just displays it, no cleanup needed there.
"""

import re

# Real ANSI escape sequences: ESC [ ... letter
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
# Leftover literal color codes when the ESC byte itself got stripped
# upstream, e.g. "[0;31mERROR:[0m" — same shape, minus \x1b.
_BARE_CODE_RE = re.compile(r"\[[0-9;]{1,8}m")
# yt-dlp prefixes like "ERROR: " / "WARNING: " (kept once, not repeated)
_LEVEL_PREFIX_RE = re.compile(r"^(ERROR|WARNING)\s*:\s*", re.IGNORECASE)

MAX_LEN = 300


def clean_error_text(raw) -> str:
    text = str(raw or "").strip()
    text = _ANSI_RE.sub("", text)
    text = _BARE_CODE_RE.sub("", text)
    # Collapse newlines/tabs/repeated spaces into a single line — this
    # is going in a one-line tooltip, not a log viewer.
    text = re.sub(r"\s+", " ", text).strip()
    text = _LEVEL_PREFIX_RE.sub("", text).strip()
    if not text:
        text = "Unknown error."
    if len(text) > MAX_LEN:
        text = text[:MAX_LEN - 1].rstrip() + "…"
    return text
