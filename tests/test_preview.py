import threading
import time

import numpy as np

from scribe.preview import LivePreview, tail_text


class FakeRecorder:
    def __init__(self, seconds, level=0.1):
        self.audio = np.full(int(seconds * 16000), level, np.float32)
        self.sr = 16000

    def snapshot(self, *, include_preroll=True):
        assert include_preroll          # 与 Recorder.end() 的起点一致, 小段位置才能对上
        return self.audio.copy()


class FakeEngine:
    def __init__(self, texts):
        self.texts = list(texts)
        self.ready, self.failed = True, False
        self.calls = 0
        self.lengths = []

    def transcribe(self, audio):
        self.calls += 1
        self.lengths.append(len(audio))
        return self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]


def wait_until(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_tail_text_keeps_end():
    assert tail_text("一二三四五", 3) == "…三四五"
    assert tail_text("一二三", 3) == "一二三"
    assert tail_text("一" * 30) == "…" + "一" * 28


def test_preview_emits_changed_text_only():
    got = []
    eng = FakeEngine(["帮我", "帮我", "帮我把"])
    p = LivePreview(eng, FakeRecorder(1.0), got.append, interval=0.01)
    p.start()
    assert wait_until(lambda: eng.calls >= 5)
    p.stop()
    assert got == ["帮我", "帮我把"]


class SplitEngine(FakeEngine):
    """正式识别走 transcribe, 字幕走 try_transcribe, 分开记长度。"""

    def __init__(self):
        super().__init__(["x"])
        self.committed, self.previewed = [], []

    def transcribe(self, audio):
        self.committed.append(len(audio))
        return "段%d。" % len(self.committed)

    def try_transcribe(self, audio):
        self.previewed.append(len(audio))
        return "尾巴"


def test_preview_caption_decodes_at_most_the_window():
    eng = SplitEngine()
    p = LivePreview(eng, FakeRecorder(6.0), lambda t: None, interval=0.01, window_s=4.0)
    p.start()
    assert wait_until(lambda: eng.previewed)
    p.stop()
    assert eng.previewed[0] == 4 * 16000 and eng.committed == []


def test_preview_skips_short_audio():
    eng = FakeEngine(["x"])
    p = LivePreview(eng, FakeRecorder(0.3), lambda t: None, interval=0.01)
    p.start()
    time.sleep(0.1)
    p.stop()
    assert eng.calls == 0


def test_preview_does_not_decode_quiet_audio_then_recovers_on_speech():
    got = []
    eng = FakeEngine(["测试"])
    rec = FakeRecorder(1.0, level=0.001)
    preview = LivePreview(eng, rec, got.append, interval=0.01)
    preview.start()
    try:
        time.sleep(0.07)
        assert not got and eng.calls == 0
        rec.audio = np.full(16000, 0.1, np.float32)
        assert wait_until(lambda: got == ["测试"])
    finally:
        preview.stop()


def test_preview_stops_cleanly():
    got = []
    n = iter(range(10 ** 6))
    eng = FakeEngine(["x"])
    eng.transcribe = lambda audio: str(next(n))      # 每次都变, 每次都会回调
    p = LivePreview(eng, FakeRecorder(1.0), got.append, interval=0.01)
    p.start()
    assert wait_until(lambda: len(got) >= 2)
    p.stop()
    count = len(got)
    time.sleep(0.03)
    assert len(got) == count


def test_no_callback_after_stop_even_mid_transcribe():
    got = []
    entered, release = threading.Event(), threading.Event()

    class Slow(FakeEngine):
        def transcribe(self, audio):
            entered.set()
            release.wait(2)
            return "迟到"

    p = LivePreview(Slow(["x"]), FakeRecorder(1.0), got.append, interval=0.01)
    p.start()
    assert entered.wait(2)
    threading.Timer(0.05, release.set).start()
    p.stop()
    time.sleep(0.1)
    assert got == []


def test_restart_after_stop():
    got = []
    eng = FakeEngine(["一"])
    p = LivePreview(eng, FakeRecorder(1.0), got.append, interval=0.01)
    p.start()
    assert wait_until(lambda: got == ["一"])
    p.stop()
    p.start()                       # 新一轮: 上一轮的文字不算"没变化"
    assert wait_until(lambda: got == ["一", "一"])
    p.stop()


def test_long_recording_is_recognized_in_segments_while_recording():
    got = []
    eng = SplitEngine()
    p = LivePreview(eng, FakeRecorder(30.0), got.append, interval=0.01)
    p.start()
    assert wait_until(lambda: got)
    session = p.stop()
    parts = session.segments(timeout=1)
    assert len(parts) >= 2 and parts[0][0] == 0
    assert all(b1 == a2 for (_, b1, _), (a2, _, _) in zip(parts, parts[1:]))
    assert all(b - a <= 12 * 16000 for a, b, _ in parts)
    assert [t for _, _, t in parts] == ["段%d，" % (i + 1) for i in range(len(parts))]
    assert got[-1].endswith("尾巴") and max(eng.previewed) <= 8 * 16000


def test_stop_without_start_returns_none_and_sessions_are_separate():
    eng = SplitEngine()
    p = LivePreview(eng, FakeRecorder(1.0), lambda t: None, interval=0.01)
    assert p.stop() is None
    p.start()
    first = p.stop()
    p.start()
    second = p.stop()
    assert first is not second
    assert first.segments(timeout=1) == [] and second.segments(timeout=1) == []


def test_session_waits_for_the_segment_in_flight():
    entered, release = threading.Event(), threading.Event()

    class SlowCommit(SplitEngine):
        def transcribe(self, audio):
            entered.set()
            release.wait(2)
            return super().transcribe(audio)

    p = LivePreview(SlowCommit(), FakeRecorder(20.0), lambda t: None, interval=0.01)
    p.start()
    assert entered.wait(2)
    session = p.stop()                    # 立即返回, 不等在途那段
    threading.Timer(0.05, release.set).start()
    parts = session.segments(timeout=2)   # 工作线程这里等它识别完
    assert len(parts) == 1 and parts[0][2] == "段1，"


def test_preview_yields_while_engine_is_busy():
    got = []
    busy = [True]

    class Shared(FakeEngine):
        def try_transcribe(self, audio):
            self.calls += 1
            return None if busy[0] else "让路后"

    eng = Shared(["x"])
    p = LivePreview(eng, FakeRecorder(1.0), got.append, interval=0.01)
    p.start()
    try:
        assert wait_until(lambda: eng.calls >= 3)
        assert got == []
        busy[0] = False
        assert wait_until(lambda: got == ["让路后"])
    finally:
        p.stop()


def test_stop_returns_without_waiting_for_decode():
    entered, release = threading.Event(), threading.Event()

    class Slow(FakeEngine):
        def transcribe(self, audio):
            entered.set()
            release.wait(2)
            return "迟到"

    p = LivePreview(Slow(["x"]), FakeRecorder(1.0), lambda t: None, interval=0.01)
    p.start()
    assert entered.wait(2)
    t = time.monotonic()
    p.stop()
    assert time.monotonic() - t < 0.05
    release.set()
