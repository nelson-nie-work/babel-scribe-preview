import numpy as np

from scribe.audio import Recorder
from scribe.capsule import CapsuleModel
from scribe.controller import Controller
from scribe.hotkey import Action as A
from scribe.pipeline import Result


class FakeEngine:
    """M2: engine 是 pipeline, 提供 process(audio, on_stage) -> Result。"""

    def __init__(self, texts, ready=True, failed=False, stage=None):
        self.texts = list(texts)
        self.ready = ready
        self.failed = failed
        self.stage = stage
        self.calls = 0

    def load(self):
        self.ready = True

    def process(self, audio, on_stage=lambda s: None):
        self.calls += 1
        if self.stage:
            on_stage(self.stage)
        item = self.texts.pop(0)
        return item if isinstance(item, Result) else Result(item, "fast")


def feed(rec, sec, amp=0.1):
    for _ in range(int(sec * 16000 / 480)):
        rec.feed(np.full(480, amp, np.float32))


def make(texts=("你好",), ready=True, result="pasted", max_s=300, deferred=False, failed=False,
         foreground=lambda: 42, stage=None):
    rec, cap, outs, jobs = Recorder(max_s=max_s), CapsuleModel(), [], []
    eng = FakeEngine(texts, ready, failed, stage)
    make.targets, make.messages, make.hooks, make.logs = [], [], [], []

    def output(text, target):
        outs.append(text)
        make.targets.append(target)
        return result

    run_async = jobs.append if deferred else (lambda fn: fn())
    c = Controller(rec, eng, output, cap, clock=lambda: 0.0, run_async=run_async, foreground=foreground,
                   on_change=lambda: make.messages.append((cap.state, cap.message)),
                   on_start=lambda: make.hooks.append("start"), on_stop=lambda: make.hooks.append("stop"),
                   log=make.logs.append)
    return c, rec, cap, eng, outs, jobs


def speak(c, rec, sec=1.0, amp=0.1):
    c.handle(A.START)
    feed(rec, sec, amp)
    c.handle(A.STOP)


def test_full_flow_delivers_text():
    c, rec, cap, eng, outs, _ = make(texts=(Result("你好", "fast"),))
    speak(c, rec)
    assert outs == ["你好"] and cap.state == "done"


def test_typed_output_uses_inserted_text_for_edit_watcher():
    c, rec, _, _, _, _ = make(texts=(Result("第一行\n第二行", "fast"),), result="typed")
    watched = []
    c.on_output = lambda text, hwnd: watched.append((text, hwnd))
    speak(c, rec)
    assert watched == [("第一行 第二行", 42)]
    assert c.learn_source(42) == "第一行 第二行"


def test_polish_stage_shows_polishing():
    c, rec, cap, eng, outs, _ = make(texts=(Result("你好。", "llm", "openai"),), stage="polish")
    speak(c, rec)
    assert ("processing", "整理中") in make.messages
    assert outs == ["你好。"] and cap.state == "done"


def test_fallback_shows_hint():
    c, rec, cap, eng, outs, _ = make(texts=(Result("你好", "fallback"),))
    speak(c, rec)
    assert outs == ["你好"]
    assert cap.state == "error" and cap.message == "整理服务不可用,已输入原文"


def test_fallback_with_paste_failure_reports_paste_failure():
    c, rec, cap, eng, outs, _ = make(texts=(Result("你好", "fallback"),), result="failed")
    speak(c, rec)
    assert cap.message == "粘贴失败"


def test_log_has_path_and_provider():
    c, rec, cap, eng, outs, _ = make(texts=(Result("你好。", "llm", "deepseek"),))
    speak(c, rec)
    assert "llm" in make.logs[-1] and "deepseek" in make.logs[-1]


def test_start_and_stop_hooks_called():
    c, rec, cap, eng, outs, _ = make()
    c.handle(A.START)
    assert make.hooks == ["start"]
    feed(rec, 1.0)
    c.handle(A.STOP)
    assert make.hooks == ["start", "stop"]
    c.handle(A.START)
    c.handle(A.CANCEL)
    assert make.hooks == ["start", "stop", "start", "stop"]


def test_over_limit_auto_stop_calls_stop_hook():
    c, rec, cap, eng, outs, _ = make(max_s=1)
    c.handle(A.START)
    feed(rec, 2.0)
    c.tick()
    assert make.hooks == ["start", "stop"]


def test_preview_shown_while_listening():
    c, rec, cap, eng, outs, _ = make()
    c.handle(A.START)
    c.on_preview("帮我")
    assert cap.preview == "帮我"


def test_preview_not_updated_after_stop():
    c, rec, cap, eng, outs, jobs = make(deferred=True)
    c.handle(A.START)
    c.on_preview("帮我")
    feed(rec, 1.0)
    c.handle(A.STOP)
    before = cap.preview
    c.on_preview("迟到")
    assert cap.preview == before


def test_too_short_cancel_outputs_nothing():
    c, rec, cap, eng, outs, _ = make()
    c.handle(A.START)
    c.handle(A.CANCEL)
    assert outs == [] and cap.state == "hidden"


def test_silent_recording_outputs_nothing():
    c, rec, cap, eng, outs, _ = make()
    speak(c, rec, sec=2.0, amp=0.001)
    assert eng.calls == 0 and outs == [] and cap.state == "hidden"


def test_overlapping_sessions_keep_order():
    c, rec, cap, eng, outs, jobs = make(texts=("一", "二"), deferred=True)
    speak(c, rec)
    speak(c, rec)
    while jobs:
        jobs.pop(0)()
    assert outs == ["一", "二"]


def test_background_result_does_not_override_listening():
    c, rec, cap, eng, outs, jobs = make(deferred=True)
    speak(c, rec)
    c.handle(A.START)
    jobs.pop(0)()
    assert outs == ["你好"] and cap.state == "listening"


def test_dictation_before_model_ready_is_processed_later():
    c, rec, cap, eng, outs, _ = make(ready=False)
    speak(c, rec)
    assert cap.state == "loading" and outs == []
    eng.ready = True
    c.tick()
    assert outs == ["你好"]


def test_over_limit_auto_stops():
    c, rec, cap, eng, outs, _ = make(max_s=1)
    c.handle(A.START)
    feed(rec, 2.0)
    c.tick()
    assert outs == ["你好"] and not rec.recording


def test_copied_only_shows_hint():
    c, rec, cap, eng, outs, _ = make(result="copied_only")
    speak(c, rec)
    assert cap.state == "error" and cap.message == "已复制,请手动粘贴"


def test_engine_error_shows_error_and_keeps_going():
    c, rec, cap, eng, outs, _ = make(texts=())
    speak(c, rec)  # FakeEngine.pop 空列表 → IndexError
    assert cap.state == "error" and cap.message == "识别出错"
    eng.texts.append("你好")
    speak(c, rec)
    assert outs == ["你好"]


def test_target_window_recorded_at_release():
    c, rec, cap, eng, outs, _ = make(foreground=lambda: 777)
    speak(c, rec)
    assert make.targets == [777]


def test_learning_uses_recent_successful_output_from_same_window_only():
    c, rec, *_ = make(texts=("把结果发给欣桥。",))
    assert c.learn_source(42) is None
    speak(c, rec)
    assert c.learn_source(42) == "把结果发给欣桥。"
    assert c.learn_source(777) is None
    c.handle(A.START)
    assert c.learn_source(42) is None
    c.handle(A.CANCEL)
    c.clock = lambda: 601.0
    assert c.learn_source(42) is None


def test_learning_does_not_use_copy_only_output():
    c, rec, *_ = make(result="copied_only")
    speak(c, rec)
    assert c.learn_source(42) is None


def test_failed_model_reports_error_and_drops_audio():
    c, rec, cap, eng, outs, _ = make(ready=False, failed=True)
    speak(c, rec)
    assert cap.state == "error" and cap.message == "模型未就绪" and outs == []
    assert len(c._queue) == 0


def test_paste_failure_shows_error():
    c, rec, cap, eng, outs, _ = make(result="failed")
    speak(c, rec)
    assert cap.state == "error" and cap.message == "粘贴失败"
