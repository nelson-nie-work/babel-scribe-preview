import numpy as np

from scribe.audio import Recorder, is_silent


def blocks(sec, amp=0.1):
    n = int(sec * 16000 / 480)
    return [np.full(480, amp, np.float32) for _ in range(n)]


def test_preroll_included():
    r = Recorder()
    for b in blocks(1.0):
        r.feed(b)
    r.begin()
    for b in blocks(0.5):
        r.feed(b)
    assert abs(len(r.end()) / 16000 - 0.8) < 0.031


def test_not_recording_keeps_only_preroll_window():
    r = Recorder()
    for b in blocks(10.0):
        r.feed(b)
    r.begin()
    assert abs(len(r.end()) / 16000 - 0.3) < 0.031


def test_cancel_discards():
    r = Recorder()
    r.begin()
    for b in blocks(1.0):
        r.feed(b)
    r.cancel()
    assert not r.recording


def test_over_limit_stops_growing():
    r = Recorder(max_s=1)
    r.begin()
    for b in blocks(2.0):
        r.feed(b)
    assert r.over_limit and len(r.end()) / 16000 <= 1.3 + 0.031


def test_is_silent_on_quiet_audio():
    assert is_silent(np.concatenate(blocks(2.0, amp=0.001)))


def test_is_silent_false_on_speech():
    assert not is_silent(np.concatenate(blocks(0.2, 0.001) + blocks(0.5, 0.05)))


def test_is_silent_on_short_click():
    assert is_silent(np.concatenate(blocks(1.0, 0.001) + blocks(0.1, 0.2)))


def test_snapshot_during_recording():
    r = Recorder()
    assert len(r.snapshot()) == 0
    for b in blocks(1.0):
        r.feed(b)
    assert len(r.snapshot()) == 0
    r.begin()
    for b in blocks(1.0):
        r.feed(b)
    snap = r.snapshot()
    assert abs(len(snap) / 16000 - 1.3) < 0.031 and r.recording
    snap[:] = 0          # 是拷贝, 改它不影响录音
    assert abs(len(r.end()) / 16000 - 1.3) < 0.031


def test_preview_snapshot_excludes_preroll_without_removing_it_from_final_audio():
    r = Recorder()
    for b in blocks(0.3, amp=0.1):
        r.feed(b)
    r.begin()
    assert not len(r.snapshot(include_preroll=False))
    quiet = blocks(0.8, amp=0.001)
    for b in quiet:
        r.feed(b)
    np.testing.assert_array_equal(r.snapshot(include_preroll=False), np.concatenate(quiet))
    assert r.end()[0] > 0.05
