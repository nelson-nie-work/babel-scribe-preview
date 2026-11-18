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


def test_micstream_unhealthy_after_callbacks_stop():
    import time
    from types import SimpleNamespace

    from scribe.audio import MicStream
    m = MicStream(Recorder())
    assert not m.healthy()
    m._stream = SimpleNamespace(active=True)
    m._last_callback = time.monotonic() - 2
    assert not m.healthy()
    m._last_callback = time.monotonic()
    assert m.healthy()
    m._stream = SimpleNamespace(active=False)
    assert not m.healthy()


def test_callback_marks_stream_alive():
    import time

    from scribe.audio import MicStream
    m = MicStream(Recorder())
    m._last_callback = 0.0
    m._callback(np.zeros((480, 1), np.float32), 480, None, None)
    assert time.monotonic() - m._last_callback < 1


def test_input_devices_refresh_reinitializes(monkeypatch):
    import sounddevice as sd

    from scribe.audio import input_devices
    calls = []
    monkeypatch.setattr(sd, "_terminate", lambda: calls.append("terminate"))
    monkeypatch.setattr(sd, "_initialize", lambda: calls.append("initialize"))
    monkeypatch.setattr(sd, "query_hostapis", lambda: [{"name": "MME"}])
    monkeypatch.setattr(sd, "query_devices", lambda: [{"name": "Mic", "hostapi": 0, "max_input_channels": 1}])
    assert input_devices(refresh=True) == {"": None, "Mic (MME)": 0}
    assert calls == ["terminate", "initialize"]
    calls.clear()
    input_devices()
    assert calls == []
