# app/downloader/queue.py
"""
Queue Manager.

Owns the rules around the Queue States (database.py owns the storage,
this module owns the *rules*):

    queued -> downloading -> converting -> completed
       |           |              |
       v           v              v
    cancelled   paused/failed   failed
       ^           |
       +-----------+ (resume -> queued)

Never invent temporary states (Rule). Every transition here is explicit
and only allowed from specific source states, so the UI/API cannot push
a job into an invalid state by accident.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database"))
import database as db  # noqa: E402

# Which states a job may move *out of* for a given user/worker action.
# (the target state is implied by the action, this just guards the source)
_ALLOWED_SOURCE = {
    "pause":  {"queued", "downloading", "converting"},
    "resume": {"paused"},
    "retry":  {"failed", "cancelled"},
    "skip":   {"queued", "paused", "failed"},
    "cancel": {"queued", "downloading", "converting", "paused"},
}

TERMINAL_STATES = {"completed", "skipped", "cancelled"}
ACTIVE_STATES = {"downloading", "converting"}


class InvalidTransition(Exception):
    pass


def _job_or_raise(job_id):
    job = db.get_job(job_id)
    if job is None:
        raise ValueError(f"job {job_id} not found")
    return job


def _guard(job, action):
    allowed = _ALLOWED_SOURCE[action]
    if job["status"] not in allowed:
        raise InvalidTransition(
            f"cannot {action} job {job['id']} from status '{job['status']}' "
            f"(allowed from: {sorted(allowed)})"
        )


# ---------------------------------------------------------------
# WORKER ASSIGNMENT
# ---------------------------------------------------------------

def get_concurrent_limit():
    return int(db.get_setting("concurrent_downloads", 3))


def claim_job(worker_name):
    """The only way a worker may take ownership of a job."""
    return db.claim_next_queued_job(worker_name)


def release_job(job_id, status="queued"):
    """Worker gives up a job (e.g. on pause/cancel) without finishing it."""
    db.update_job(job_id, status=status, locked_by=None, locked_at=None)


def mark_downloading(job_id):
    db.update_job(job_id, status="downloading")


def mark_converting(job_id):
    db.update_job(job_id, status="converting")


def mark_completed(job_id, file_path):
    db.update_job(job_id, status="completed", progress_percent=100,
                  file_path=file_path, locked_by=None, locked_at=None, error_message=None)


def mark_failed(job_id, error_message):
    db.update_job(job_id, status="failed", error_message=str(error_message),
                  locked_by=None, locked_at=None)


def set_progress(job_id, percent):
    db.set_progress(job_id, percent)


def mark_used_cookies(job_id):
    db.update_job(job_id, used_cookies=1)


# ---------------------------------------------------------------
# USER ACTIONS (UI -> here, never UI -> yt-dlp directly)
# ---------------------------------------------------------------

def pause_job(job_id):
    job = _job_or_raise(job_id)
    _guard(job, "pause")
    db.update_job(job_id, status="paused", locked_by=None, locked_at=None)
    return db.get_job(job_id)


def resume_job(job_id):
    job = _job_or_raise(job_id)
    _guard(job, "resume")
    db.update_job(job_id, status="queued")
    return db.get_job(job_id)


def retry_job(job_id):
    job = _job_or_raise(job_id)
    _guard(job, "retry")
    db.update_job(
        job_id, status="queued", progress_percent=0, error_message=None,
        locked_by=None, locked_at=None, retry_count=job["retry_count"] + 1,
    )
    return db.get_job(job_id)


def skip_job(job_id):
    job = _job_or_raise(job_id)
    _guard(job, "skip")
    db.update_job(job_id, status="skipped", locked_by=None, locked_at=None)
    return db.get_job(job_id)


def cancel_job(job_id):
    job = _job_or_raise(job_id)
    _guard(job, "cancel")
    db.update_job(job_id, status="cancelled", locked_by=None, locked_at=None)
    return db.get_job(job_id)


def delete_job(job_id):
    """Delete is allowed from any state — it's a hard removal, not a
    queue transition. The caller (API layer) deals with stopping an
    in-progress download / removing files before calling this."""
    _job_or_raise(job_id)
    db.delete_job(job_id)


# ---------------------------------------------------------------
# QUEUE VIEWS
# ---------------------------------------------------------------

def get_queue(group_id=None):
    return db.list_jobs(group_id=group_id)


def get_active_jobs():
    return db.list_jobs(status=list(ACTIVE_STATES))


def get_queued_jobs():
    return db.list_jobs(status="queued")


def queue_stats():
    all_jobs = db.list_jobs()
    stats = {"total": len(all_jobs)}
    for j in all_jobs:
        stats[j["status"]] = stats.get(j["status"], 0) + 1
    return stats


# ---------------------------------------------------------------
# STARTUP RECOVERY
# ---------------------------------------------------------------

def reset_stale_locks():
    """Call once when the app starts. Any job still marked
    'downloading'/'converting' from a previous run has no real worker
    behind it anymore (the process restarted) — put it back in the
    queue so workers pick it up again. This is what makes
    'Auto Resume After Restart' possible without extra bookkeeping,
    since the database is the single source of truth (Rule 2)."""
    stale = db.list_jobs(status=list(ACTIVE_STATES))
    for job in stale:
        db.update_job(job["id"], status="queued", locked_by=None, locked_at=None)
    return len(stale)
