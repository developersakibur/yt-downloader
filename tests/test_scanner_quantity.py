# tests/test_scanner_quantity.py
"""
Covers scan_preview()'s quantity-cap logic — search capped at 250,
playlist/channel 'All' capped at 999 — without hitting the network.
scanner.probe() is monkeypatched to a fake that just records what
quantity it was asked for and returns an empty result, so these tests
run in milliseconds and don't depend on YouTube being reachable.

This is exactly the logic that regressed earlier in this project's
history (search silently capped playlists at 100) — worth pinning down.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "scanner"))
import scanner  # noqa: E402


def _fake_probe(recorder):
    def probe(extract_url, flat, quantity=100, on_progress=None, prefer_cookies=False, on_chunk=None):
        recorder["quantity"] = quantity
        recorder["prefer_cookies"] = prefer_cookies
        return {"entries": [], "title": "Fake Title"}, False
    return probe


def test_search_quantity_capped_at_250(monkeypatch):
    recorder = {}
    monkeypatch.setattr(scanner, "probe", _fake_probe(recorder))
    scanner.scan_preview("https://www.youtube.com/results?search_query=test", quantity=99999)
    assert recorder["quantity"] == 250


def test_search_quantity_below_cap_passes_through_unchanged(monkeypatch):
    recorder = {}
    monkeypatch.setattr(scanner, "probe", _fake_probe(recorder))
    scanner.scan_preview("https://www.youtube.com/results?search_query=test", quantity=50)
    assert recorder["quantity"] == 50


def test_playlist_all_capped_at_999(monkeypatch):
    recorder = {}
    monkeypatch.setattr(scanner, "probe", _fake_probe(recorder))
    scanner.scan_preview("https://www.youtube.com/playlist?list=PLxxxxxxxxxxxxxxxxxxxxxxx",
                          quantity="all")
    assert recorder["quantity"] == 999


def test_playlist_explicit_quantity_capped_at_999_even_if_larger(monkeypatch):
    recorder = {}
    monkeypatch.setattr(scanner, "probe", _fake_probe(recorder))
    scanner.scan_preview("https://www.youtube.com/playlist?list=PLxxxxxxxxxxxxxxxxxxxxxxx",
                          quantity=5000)
    assert recorder["quantity"] == 999


def test_playlist_scan_prefers_cookies_upfront(monkeypatch):
    # Bulk playlist/channel scans should send cookies from the first
    # attempt (see cookies.py docstring) — search should not.
    recorder = {}
    monkeypatch.setattr(scanner, "probe", _fake_probe(recorder))
    scanner.scan_preview("https://www.youtube.com/playlist?list=PLxxxxxxxxxxxxxxxxxxxxxxx",
                          quantity="all")
    assert recorder["prefer_cookies"] is True


def test_search_scan_does_not_prefer_cookies(monkeypatch):
    recorder = {}
    monkeypatch.setattr(scanner, "probe", _fake_probe(recorder))
    scanner.scan_preview("https://www.youtube.com/results?search_query=test", quantity=25)
    assert recorder["prefer_cookies"] is False


def test_invalid_url_raises_value_error():
    try:
        scanner.scan_preview("https://example.com/not-youtube")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_scan_preview_emits_entries_one_at_a_time_per_chunk(monkeypatch, temp_db):
    """Pins down the Task 2 behavior: on_entries must fire once per VIDEO,
    as soon as each chunk lists it — not once per chunk, and not only
    after the whole listing finishes. Fakes probe() to call on_chunk
    twice (simulating two ~100-item pages), and checks entries stream
    out immediately, in order, with playlist_index continuing across
    chunk boundaries."""
    monkeypatch.setattr(scanner, "download_thumbnail", lambda video_id, url: None)

    def fake_probe(extract_url, flat, quantity=100, on_progress=None, prefer_cookies=False, on_chunk=None):
        chunk_1 = [{"id": "vid1", "url": "https://youtu.be/vid1", "title": "One"},
                   {"id": "vid2", "url": "https://youtu.be/vid2", "title": "Two"}]
        chunk_2 = [{"id": "vid3", "url": "https://youtu.be/vid3", "title": "Three"}]
        if on_chunk:
            on_chunk(chunk_1, None)
            on_chunk(chunk_2, None)
        return {"entries": chunk_1 + chunk_2, "title": "Fake Playlist"}, False

    monkeypatch.setattr(scanner, "probe", fake_probe)

    emitted_calls = []  # each element is the list on_entries was called with
    scanner.scan_preview(
        "https://www.youtube.com/playlist?list=PLxxxxxxxxxxxxxxxxxxxxxxx",
        quantity=10,
        on_entries=lambda new_entries: emitted_calls.append(new_entries),
    )

    # One call per video, not one call per chunk (i.e. not [chunk_1_entries], [chunk_2_entries])
    assert len(emitted_calls) == 3
    assert all(len(call) == 1 for call in emitted_calls)
    assert [call[0]["video_id"] for call in emitted_calls] == ["vid1", "vid2", "vid3"]
    # playlist_index keeps counting across the chunk boundary instead of resetting
    assert [call[0]["playlist_index"] for call in emitted_calls] == [1, 2, 3]


# ── Task 3: Mix/Radio playlist detection + safety net ───────────────

def test_detect_type_recognizes_mix_from_watch_url():
    url = "https://www.youtube.com/watch?v=4YPaFrWf9OM&list=RD4YPaFrWf9OM&start_radio=1"
    assert scanner.detect_type(url) == "mix"
    # A Mix always wins over the normal single/playlist branches,
    # regardless of the scope toggle:
    assert scanner.detect_type(url, force_playlist=True) == "mix"


def test_detect_type_recognizes_mix_from_playlist_url():
    url = "https://www.youtube.com/playlist?list=RDCLAK5uy_abc123"
    assert scanner.detect_type(url) == "mix"


def test_detect_type_normal_playlist_is_not_mix():
    url = "https://www.youtube.com/watch?v=abc123&list=PLxxxxxxxxxxxxxxxxxxxxxxx"
    assert scanner.detect_type(url, force_playlist=True) == "playlist"


def test_mix_quantity_ignores_all_and_falls_back_to_default(monkeypatch):
    """A Mix has no 'All' concept — the UI should never send it, but if it
    somehow does, it must NOT be treated as 999/unlimited like a real
    playlist's 'All'. It just falls back to the normal default quantity."""
    recorder = {}
    monkeypatch.setattr(scanner, "probe", _fake_probe(recorder))
    scanner.scan_preview(
        "https://www.youtube.com/watch?v=4YPaFrWf9OM&list=RD4YPaFrWf9OM",
        quantity="all", force_playlist=True,
    )
    assert recorder["quantity"] == 25  # default fallback, nowhere near GROUP_MAX (999)


def test_mix_quantity_hard_capped_even_for_large_explicit_number(monkeypatch):
    """Even an explicit large request for a Mix is capped at MIX_MAX (100)
    — well below a real playlist/channel's 999 ceiling — since a Mix has
    no real fixed length to justify going that high."""
    recorder = {}
    monkeypatch.setattr(scanner, "probe", _fake_probe(recorder))
    scanner.scan_preview(
        "https://www.youtube.com/watch?v=4YPaFrWf9OM&list=RD4YPaFrWf9OM",
        quantity=5000, force_playlist=True,
    )
    assert recorder["quantity"] == 100


def test_overscan_safety_net_stops_when_actual_far_exceeds_reported_count(monkeypatch, temp_db):
    """If the reported playlist_count (e.g. the 25 YouTube's sidebar
    showed) turns out to be far smaller than what's actually being
    found — the exact Mix/Radio symptom from the bug report — scanning
    should stop instead of silently walking to the hard cap."""
    monkeypatch.setattr(scanner, "download_thumbnail", lambda video_id, url: None)

    def fake_probe(extract_url, flat, quantity=100, on_progress=None, prefer_cookies=False, on_chunk=None):
        reported_count = 25  # what the initial metadata claimed
        stopped = False
        entries = []
        for chunk_start in range(0, 600, 100):
            if stopped:
                break
            chunk = [{"id": f"vid{i}", "url": f"https://youtu.be/vid{i}"}
                     for i in range(chunk_start, chunk_start + 100)]
            entries.extend(chunk)
            if on_chunk and on_chunk(chunk, reported_count) is False:
                stopped = True
        return {"entries": entries, "title": "Fake Mix", "stopped_early": stopped}, False

    monkeypatch.setattr(scanner, "probe", fake_probe)

    result = scanner.scan_preview(
        "https://www.youtube.com/watch?v=x&list=RDx", quantity=100, force_playlist=True,
    )
    # Stopped well short of the full 600 fake entries (3x the reported
    # 25 = 75, so it should stop partway through the very first chunk).
    assert len(result["entries"]) <= 100
    assert len(result["entries"]) < 600
    assert result["stopped_early"] is True


def test_single_video_url_rejected_by_scan_preview(monkeypatch):
    # scan_preview is for playlist/search/channel only — a single video
    # URL should raise, not silently do something unexpected.
    try:
        scanner.scan_preview("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_detect_type_playlist_vs_video_vs_video_with_list():
    assert scanner.detect_type("https://www.youtube.com/playlist?list=PLabc") == "playlist"
    assert scanner.detect_type("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "single"
    # Backend only distinguishes single vs playlist via force_playlist —
    # "video+list" as a scope choice is a frontend-only concept (the
    # web/extension UI's "This video only" vs "Whole playlist" toggle);
    # scanner.detect_type() itself never returns that string.
    assert scanner.detect_type("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc") == "single"
    # ...but with force_playlist=True (user picked "Whole playlist"), it's a playlist scan
    assert scanner.detect_type("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc",
                                force_playlist=True) == "playlist"
