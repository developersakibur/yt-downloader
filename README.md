# Tests

Run all of them:

```
pip install pytest --break-system-packages
python -m pytest
```

## What's covered

- **test_scanner_quantity.py** — the quantity-cap rules (search max 250,
  playlist/channel "All" max 999) and `detect_type()`'s URL classification.
  `scanner.probe()` is mocked, so these never touch the network.
- **test_converter_state_machine.py** — batch converter pause/resume/skip/delete
  transitions and the guard rules for which states each action is valid from.
- **test_job_queue_retry.py** — the auto-retry backoff schedule
  (5s / 30s / 120s), `next_retry_at` handling, and global pause-all/resume-all.

## What's NOT covered (needs real yt-dlp/ffmpeg, out of scope for unit tests)

- Actual video downloads or conversions
- The retry-sweep background loop actually firing on a timer (logic is
  tested directly instead — see `test_get_jobs_due_for_retry_only_returns_elapsed_timers`)
- The SSE endpoint's streaming behavior (would need a running Flask app + long-lived connection)

## Why one test occasionally logs a thread warning

`test_resume_paused_job` calls the real `resume_job()`, which spawns a real
worker thread if none are alive for that batch — same as production. That
thread tries to claim work against the test's temporary database, which gets
deleted right after the test finishes. You may see a
`PytestUnhandledThreadExceptionWarning` about "no such table" in the output.
It's harmless (the test still passes — the exception is in a detached daemon
thread, not the test's own call stack) and is a side effect of exercising
real behavior instead of mocking it away.
