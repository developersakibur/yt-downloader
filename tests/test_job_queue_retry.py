# tests/test_job_queue_retry.py
"""
Covers job_queue.py's retry/backoff logic — this is exactly the kind of
thing that's easy to silently break (e.g. forgetting to clear
next_retry_at on manual retry would leave a job looking like it's still
waiting on a timer that already fired).
"""

import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "downloader"))
import job_queue as q  # noqa: E402


def _make_job(temp_db, status="queued", retry_count=0, video_id="abc123"):
    gid = temp_db.create_group(type_="playlist", name="Test Group", source_url="https://x")
    job_id = temp_db.create_job(
        group_id=gid, video_id=video_id, url="https://youtu.be/" + video_id,
        original_title="Test video", format="MP4", quality="best",
    )
    if status != "queued" or retry_count:
        temp_db.update_job(job_id, status=status, retry_count=retry_count)
    return job_id


def test_schedule_retry_sets_next_retry_at_and_keeps_status_failed(temp_db):
    job_id = _make_job(temp_db)
    delay = q.schedule_retry(job_id, "Connection timed out")
    assert delay == 5  # first attempt -> 5s per BACKOFF_SCHEDULE_SECONDS

    job = temp_db.get_job(job_id)
    assert job["status"] == "failed"
    assert job["next_retry_at"] is not None
    assert job["error_message"] == "Connection timed out"


def test_schedule_retry_backoff_increases_with_retry_count(temp_db):
    job_id = _make_job(temp_db, retry_count=1)
    delay = q.schedule_retry(job_id, "HTTP Error 429")
    assert delay == 30  # second attempt

    job_id2 = _make_job(temp_db, retry_count=2, video_id="def456")
    delay2 = q.schedule_retry(job_id2, "HTTP Error 429")
    assert delay2 == 120  # third attempt


def test_schedule_retry_returns_none_when_budget_exhausted(temp_db):
    job_id = _make_job(temp_db, retry_count=q.MAX_AUTO_RETRIES)
    delay = q.schedule_retry(job_id, "still failing")
    assert delay is None
    # Exhausted budget shouldn't have mutated anything — caller (worker.py)
    # is expected to fall through to a normal permanent mark_failed().
    job = temp_db.get_job(job_id)
    assert job["next_retry_at"] is None


def test_retry_job_clears_next_retry_at_and_bumps_count(temp_db):
    job_id = _make_job(temp_db, status="failed", retry_count=1)
    temp_db.update_job(job_id, next_retry_at="2020-01-01 00:00:00")

    updated = q.retry_job(job_id)

    assert updated["status"] == "queued"
    assert updated["retry_count"] == 2
    assert updated["next_retry_at"] is None
    assert updated["error_message"] is None


def test_retry_job_rejects_invalid_source_status(temp_db):
    job_id = _make_job(temp_db, status="completed")
    try:
        q.retry_job(job_id)
        assert False, "expected InvalidTransition"
    except q.InvalidTransition:
        pass


def test_get_jobs_due_for_retry_only_returns_elapsed_timers(temp_db):
    due_job = _make_job(temp_db, status="failed", video_id="due1")
    not_due_job = _make_job(temp_db, status="failed", video_id="notdue1")

    past = (datetime.now() - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S")
    future = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    temp_db.update_job(due_job, next_retry_at=past)
    temp_db.update_job(not_due_job, next_retry_at=future)

    due = temp_db.get_jobs_due_for_retry()
    due_ids = {j["id"] for j in due}

    assert due_job in due_ids
    assert not_due_job not in due_ids


def test_pause_all_and_resume_all(temp_db):
    j1 = _make_job(temp_db, status="queued", video_id="p1")
    j2 = _make_job(temp_db, status="downloading", video_id="p2")
    j3 = _make_job(temp_db, status="completed", video_id="p3")  # shouldn't be touched

    paused = q.pause_all()
    assert paused == 2
    assert temp_db.get_job(j1)["status"] == "paused"
    assert temp_db.get_job(j2)["status"] == "paused"
    assert temp_db.get_job(j3)["status"] == "completed"

    resumed = q.resume_all()
    assert resumed == 2
    assert temp_db.get_job(j1)["status"] == "queued"
    assert temp_db.get_job(j2)["status"] == "queued"
