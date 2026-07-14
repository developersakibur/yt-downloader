# tests/test_converter_state_machine.py
"""
Covers converter.py's pause/resume/skip/delete transitions — the part
of this project most likely to regress silently, since Windows has no
real process pause/resume and the whole design hinges on getting the
'converting' vs 'queued'/'paused' guard logic exactly right.

These tests exercise the DB-level state transitions directly (via
create_local_conversion_job) rather than start_batch(), since start_batch
needs real video files + ffmpeg on disk — out of scope for a unit test.
No job here is ever actually 'converting' with a real ffmpeg process
behind it, so _kill_running() always finds nothing to kill (returns
False) and pause/skip/delete on a 'converting' row just falls through
to a plain status flip — that path is exercised separately in
test_delete_converting_job_without_live_process.

Each test gets its own unique batch_id (see _make_conv_job) — reusing
one across tests let a leftover background worker thread spawned by an
earlier test's resume_job() call claim and mutate a LATER test's job
row mid-assertion (both in the same 'batch1'), causing rare flaky
failures. Unique batch_id per test means a thread can only ever touch
rows from the test that spawned it.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "converter"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "downloader"))
import converter as c  # noqa: E402


import uuid


def _make_conv_job(temp_db, status="queued", batch_id=None):
    if batch_id is None:
        batch_id = f"batch-{uuid.uuid4()}"  # unique per call — see module docstring
    return temp_db.create_local_conversion_job(
        batch_id=batch_id, source_path=f"/videos/{status}_{id(object())}.mp4",
        source_filename="test.mp4", target_format="MP3", quality="192k",
        output_path=f"/videos/MP3/test_{status}.mp3", status=status,
    )


def test_pause_queued_job_is_plain_status_flip(temp_db):
    job_id = _make_conv_job(temp_db, status="queued")
    result = c.pause_job(job_id)
    assert result["status"] == "paused"


def test_resume_paused_job(temp_db):
    # Note: resume_job() spawns a real worker thread (see converter.py)
    # since no workers are alive for this batch — that thread will try
    # to claim the next job and, once this test's temp_db is torn down,
    # may log a harmless "no such table" thread warning in test output.
    # It doesn't fail the test (the exception is in a detached daemon
    # thread), just noise — a consequence of testing resume_job's real
    # production behavior rather than mocking it away.
    job_id = _make_conv_job(temp_db, status="paused")
    result = c.resume_job(job_id)
    assert result["status"] == "queued"
    assert result["progress_percent"] == 0


def test_resume_rejects_non_paused_source(temp_db):
    job_id = _make_conv_job(temp_db, status="completed")
    try:
        c.resume_job(job_id)
        assert False, "expected InvalidTransition"
    except c.InvalidTransition:
        pass


def test_skip_queued_job(temp_db):
    job_id = _make_conv_job(temp_db, status="queued")
    result = c.skip_job(job_id)
    assert result["status"] == "skipped"


def test_pause_completed_job_rejected(temp_db):
    job_id = _make_conv_job(temp_db, status="completed")
    try:
        c.pause_job(job_id)
        assert False, "expected InvalidTransition"
    except c.InvalidTransition:
        pass


def test_delete_queued_job_removes_row(temp_db):
    job_id = _make_conv_job(temp_db, status="queued")
    c.delete_job(job_id)
    assert temp_db.get_local_conversion_job(job_id) is None


def test_delete_converting_job_without_live_process_still_removes_row(temp_db):
    # No real ffmpeg process is registered for this job_id in
    # _active_processes, so _kill_running() returns False — delete_job
    # should still succeed and clean up the row (just skips the kill step).
    job_id = _make_conv_job(temp_db, status="converting")
    c.delete_job(job_id)
    assert temp_db.get_local_conversion_job(job_id) is None


def test_pause_all_allowed_source_states_are_queued_and_converting():
    assert c._ALLOWED_SOURCE["pause"] == {"queued", "converting"}


def test_delete_allowed_from_every_terminal_and_active_state():
    assert c._ALLOWED_SOURCE["delete"] == {
        "queued", "converting", "paused", "completed", "failed", "skipped"
    }


def test_unknown_job_id_raises_value_error(temp_db):
    try:
        c.pause_job(999999)
        assert False, "expected ValueError"
    except ValueError:
        pass
